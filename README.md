# Rime 配置更新模板

用于Rime的自定义配置管理、备份：
将自定义配置和第三方输入方案配置分开管理，最后通过脚本拷贝/合并到指定文件夹中，减少更新第三方方案时带来的冲突。

根据 `init.yaml` 管理子模块（或软链接），并将各子模块内容合并到指定目标目录，用于 Rime 等配置的集中更新与部署。

## 依赖

- Python 3
- PyYAML（`pip install pyyaml`）

## 配置

`init.yaml`，主要结构：

- **init**：主库与全局设置
  - `git-url` / `git-branch` / `git-remote`：主库远程与分支
  - `target-folder`：合并输出的目标目录（update 时用）
  - `backup-folder`：更新前备份目录
- **subdir**：子项列表，每项支持：
  - `type: gitsubmodule`：Git 子模块，需配置 `url`、`branch`
  - `type: ln`：软链接，`url` 填目标绝对路径
  - `path`：本地路径；`ignore`：合并时忽略的模式；`symlink`：在目标处建软链接的路径；`unzip-file`：可选，zip 路径，解压到该子模块并去壳

## 使用

**初始化**（克隆/添加子模块、软链接，主库提交）：

```bash
python scripts/init.py
```

**更新并合并到目标目录**：

```bash
python scripts/update.py
```

常用参数：

- `-t, --target <目录>`：覆盖 `init.target-folder`
- `--no-backup`：不备份目标目录
- `-u, --unzip`：按配置解压 zip 到对应子模块并提交
- `-p, --push`：合并后对各子模块和主库执行 add/commit/push

示例：

```bash
python scripts/update.py -t /path/to/rime
python scripts/update.py -u -p
```
