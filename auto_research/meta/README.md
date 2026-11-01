# Meta-loop 工具

`meta_loop.py` 是历史留下的通用沙箱 patch/A-B runner，可在隔离工作树中执行单文件补丁、候选比较和恢复检查。当前研究架构复用 harness² 的 Propose–Probe–Compose，具体入口见 [../docs/DESIGN_CN.md](../docs/DESIGN_CN.md)。

公开快照包含该工具源码，不包含历史 run、prereg、proposal、claim、reviewer 或检索数据库。当前探索不为每个步骤建立完整证明链；运行方式和输入契约以 `meta_loop.py --help` 与源码为准。
