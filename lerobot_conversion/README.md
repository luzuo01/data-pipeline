# embodied_gt_v1 → LeRobot v3

本目录包含转换器 `convert_hdf5.py`、独立验证器 `validate_conversion.py`、源真值读取器 `source_ground_truth.py`。固定 LeRobot commit 见 requirements.txt；完整环境快照见 requirements.lock.txt。

```powershell
# 仓库根目录；输出目录必须不存在
lerobot_conversion\.venv\Scripts\python.exe lerobot_conversion/convert_hdf5.py <source.h5> <new-output-directory>
lerobot_conversion\.venv\Scripts\python.exe lerobot_conversion/validate_conversion.py <new-output-directory>
```

适配器针对 embodied_gt_v1，不是任意 HDF5 转换器。执行成功、动作语义、相机字段必须来自源数据，不能靠转换补造。详见 [数据契约](../docs/DATA_CONTRACT.md) 和 [安装说明](../docs/SETUP.md)。
