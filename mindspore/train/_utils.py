# Copyright 2020 Huawei Technologies Co., Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ============================================================================
"""Train utility."""
import os
from collections.abc import Iterable

import numpy as np

from mindspore.common.tensor import Tensor
from mindspore.common.dtype import dtype_to_nptype, pytype_to_dtype
from mindspore.common import dtype as mstype
from mindspore import log as logger
from mindspore.common.api import _executor

from .lineage_pb2 import DatasetGraph, TrainLineage, EvaluationLineage, UserDefinedInfo

def _convert_type(types):
    """
    Convert from numpy type to tensor type.

    Args:
        types (list): Numpy type list of element in dataset.

    Returns:
        list, list of element in dataset.
    """
    ms_types = []
    for np_type in types:
        ms_type = pytype_to_dtype(np_type)
        ms_types.append(ms_type)
    return ms_types


def _get_types_and_shapes(dataset):
    """Get dataset types and shapes."""
    dataset_types = _convert_type(dataset.output_types())
    dataset_shapes = dataset.output_shapes()
    return dataset_types, dataset_shapes


def _exec_datagraph(exec_dataset, dataset_size, phase='dataset'):
    """Initialize and execute the dataset graph."""
    batch_size = exec_dataset.get_batch_size()
    input_indexs = exec_dataset.input_indexs

    # transform data format
    dataset_types, dataset_shapes = _get_types_and_shapes(exec_dataset)
    send_epoch_end = bool(dataset_size == -1)
    exec_dataset = exec_dataset.device_que(send_epoch_end=send_epoch_end)

    _executor.init_dataset(exec_dataset.queue_name,
                           dataset_size,
                           batch_size,
                           dataset_types,
                           dataset_shapes,
                           input_indexs,
                           phase=phase)

    return exec_dataset


def _make_directory(path: str):
    """Make directory."""
    real_path = None
    if path is None or not isinstance(path, str) or path.strip() == "":
        logger.error("The path(%r) is invalid type.", path)
        raise TypeError("Input path is invaild type")

    # convert the relative paths
    path = os.path.realpath(path)
    logger.debug("The abs path is %r", path)

    # check the path is exist and write permissions?
    if os.path.exists(path):
        real_path = path
    else:
        # All exceptions need to be caught because create directory maybe have some limit(permissions)
        logger.debug("The directory(%s) doesn't exist, will create it", path)
        try:
            os.makedirs(path, exist_ok=True)
            real_path = path
        except PermissionError as e:
            logger.error("No write permission on the directory(%r), error = %r", path, e)
            raise TypeError("No write permission on the directory.")
    return real_path


def _construct_tensor_list(types, shapes, batch_expand_num=1):
    """
    Construct list of tensors with types and shapes, used to initialize the network.

    Args:
        types: List or Tuple. The output types of element in dataset.
        shapes: List or Tuple. The output shapes of element in dataset.
        batch_expand_num (int): Batch expand number.

    Returns:
        List, list of Tensors.
    """
    if len(types) != len(shapes):
        raise ValueError("The length of dataset types must equal to dataset shapes, "
                         "but got dataset types={} and dataset shapes={}".format(types, shapes))
    tensor_list = []
    for type_, shape in zip(types, shapes):
        new_shape = ()
        for i, item in enumerate(shape):
            if i == 0:
                new_shape += (item * batch_expand_num,)
            else:
                new_shape += (item,)
        tensor = Tensor(np.zeros(new_shape, dtype_to_nptype(type_)))
        tensor.virtual_flag = True
        tensor_list.append(tensor)
    return tensor_list


def _to_tensor(elem, scaling_sens=None):
    """Convert numpy to tensor, adapt to feed the data from host solution."""
    lst = []
    if not isinstance(elem, (tuple, list)):
        elem = [elem]
    for data in elem:
        if not isinstance(data, np.ndarray):
            if scaling_sens:
                elem_tuple = tuple(elem) + (Tensor(scaling_sens, mstype.float32),)
            else:
                elem_tuple = tuple(elem)
            return elem_tuple
        lst.append(Tensor(data))
    if scaling_sens:
        lst.append(Tensor(scaling_sens, mstype.float32))

    return lst[0] if len(lst) == 1 else tuple(lst)


def _to_full_tensor(elem, device_num, global_rank, scaling_sens=None):
    """Convert numpy to tensor, expanding batch dimension according to device_num, adapt to feed the data
       from host solution."""
    lst = []
    if not isinstance(elem, (tuple, list)):
        elem = [elem]
    if global_rank >= device_num:
        raise ValueError("The global rank must be smaller than device number, the global rank is {}, "
                         "the device num is {}".format(global_rank, device_num))

    for data in elem:
        if isinstance(data, np.ndarray):
            data = Tensor(data)
        if not isinstance(data, Tensor):
            raise ValueError("elements in tensors must be Tensor")
        shape_ = data.shape
        type_ = data.dtype
        new_shape = ()
        batchsize_per_device = 1
        for i, item in enumerate(shape_):
            if i == 0:
                new_shape += (item * device_num,)
                batchsize_per_device = item
            else:
                new_shape += (item,)
        new_tensor_numpy = np.zeros(new_shape, dtype_to_nptype(type_))
        start = global_rank * batchsize_per_device
        new_tensor_numpy[start: start + batchsize_per_device] = data.asnumpy()
        new_tensor = Tensor(new_tensor_numpy)
        lst.append(new_tensor)
    if scaling_sens:
        lst.append(Tensor(scaling_sens, mstype.float32))
    return tuple(lst)


def _construct_input_tensors(dataset_types, dataset_shapes, device_number=1):
    """Construct tensor list to initialize the network which implemented in dataset sink."""
    tensor_list_run = _construct_tensor_list(dataset_types, dataset_shapes, batch_expand_num=1)
    tensor_list_compile = _construct_tensor_list(dataset_types, dataset_shapes, batch_expand_num=device_number)
    return tensor_list_run, tensor_list_compile


def _to_full_shapes(shapes, device_num):
    """Expanding batch dimension according to device_num, adapt to mindspore minddata graph solution."""
    new_shapes = []
    for shape in shapes:
        new_shape = ()
        for i, item in enumerate(shape):
            if i == 0:
                new_shape += (item * device_num,)
            else:
                new_shape += (item,)
        new_shapes.append(new_shape)
    return new_shapes


def _check_to_numpy(plugin, tensor):
    """Check the tensor and return a numpy.ndarray."""
    np_value = tensor.asnumpy()
    if plugin == 'scalar':
        if np_value.size == 1:
            return np_value
        raise ValueError('The tensor holds more than one value, but the scalar plugin expects on value.')
    if plugin == 'image':
        if np_value.ndim == 4:
            return np_value
        raise ValueError('The tensor seems not to hold a valid image.')
    if plugin in ('tensor', 'histogram'):
        if np_value.ndim > 0:
            return np_value
        raise ValueError('The tensor should not be empty.')
    return np_value


def _check_lineage_value(plugin, value):
    """Check the lineage value."""
    def raises(plugin, prototype):
        raise TypeError(f'Plugin {repr(plugin)} expects a {prototype.__name__} value.')

    if plugin == 'dataset_graph' and not isinstance(value, DatasetGraph):
        raises(plugin, DatasetGraph)

    if plugin == 'eval_lineage' and not isinstance(value, EvaluationLineage):
        raises(plugin, EvaluationLineage)

    if plugin == 'train_lineage' and not isinstance(value, TrainLineage):
        raises(plugin, TrainLineage)

    if plugin == 'custom_lineage_data' and not isinstance(value, UserDefinedInfo):
        raises(plugin, UserDefinedInfo)


def check_value_type(arg_name, arg_value, valid_types):
    """Checks whether a value is instance of some types."""
    valid_types = tuple(valid_types) if isinstance(valid_types, Iterable) else (valid_types,)
    is_valid = True

    # bool is subclass of int, so for a bool value, we need to extra check
    if isinstance(arg_value, int) and isinstance(arg_value, bool) and bool not in valid_types:
        is_valid = False

    if not isinstance(arg_value, valid_types):
        is_valid = False

    if not is_valid:
        raise TypeError(f'For `{arg_name}` the type should be a valid type of {[t.__name__ for t in valid_types]}, '
                        f'bug got {type(arg_value).__name__}.')
