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

"""PReLUGrad op"""
from mindspore.ops.op_info_register import op_info_register, TBERegOp, DataType

prelu_grad_op_info = TBERegOp("PReLUGrad") \
    .fusion_type("ELEMWISE") \
    .async_flag(False) \
    .binfile_name("prelu_grad.so") \
    .compute_cost(10) \
    .kernel_name("prelu_grad") \
    .partial_flag(True) \
    .input(0, "grads", False, "required", "all") \
    .input(1, "features", False, "required", "all") \
    .input(2, "weights", False, "required", "all") \
    .output(0, "dx", False, "required", "all") \
    .output(0, "da", False, "required", "all") \
    .dtype_format(DataType.F32_NCHW, DataType.F32_NCHW, DataType.F32_Default,
                  DataType.F32_NCHW, DataType.F32_Default) \
    .dtype_format(DataType.F32_5HD, DataType.F32_5HD, DataType.F32_5HD,
                  DataType.F32_5HD, DataType.F32_5HD) \
    .dtype_format(DataType.F32_Default, DataType.F32_Default, DataType.F32_Default,
                  DataType.F32_Default, DataType.F32_Default) \
    .get_op_info()


@op_info_register(prelu_grad_op_info)
def _prelu_grad_tbe():
    """PReLUGrad TBE register"""
    return