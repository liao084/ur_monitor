# UR Monitor

用于监控 Windows 业务环境中的 `UR实时采集.exe` 和 `牛.exe`：

- UR 不存在时，按照配置的绝对路径启动 UR；
- UR 存在但牛连续缺失达到阈值时，通过 UI Automation 调用“打开脚本”按钮；
- 正常巡检不写日志，仅记录异常和恢复动作。

复制 `monitor_config.example.json` 为 `monitor_config.json`，并根据实际环境修改配置。真实配置和交付目录不会提交到 Git。
