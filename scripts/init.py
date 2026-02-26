#!/usr/bin/env python3
"""根据 init.yaml 初始化子模块或软链接。可在仓库根目录执行，或用绝对路径从任意目录执行。"""
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("init")


def find_repo_root() -> Path:
    """优先从当前目录及父目录查找含 init.yaml 的仓库根，否则用脚本所在仓库。"""
    for d in [Path.cwd()] + list(Path.cwd().resolve().parents):
        if (d / "init.yaml").exists():
            return d
    return SCRIPT_DIR.parent


def load_config(repo_root: Path):
    init_yaml = repo_root / "init.yaml"
    if not init_yaml.exists():
        log.error("未找到 %s", init_yaml)
        sys.exit(1)
    with open(init_yaml, encoding="utf-8") as f:
        return yaml.safe_load(f)


def is_git_repo(path: Path) -> bool:
    return (path / ".git").exists()


def init_main_repo(init_cfg: dict, repo_root: Path):
    """根据 init 配置设置主库的 remote 与 branch。"""
    remote = (init_cfg.get("git-remote") or "origin").strip()
    branch = (init_cfg.get("git-branch") or "").strip()
    if not branch:
        log.warning("init 配置中 git-branch 为空，设置为 main")
        branch = "main"
    url = (init_cfg.get("git-url") or "").strip()
    if not url:
        log.warning("init 配置中 git-url 为空，跳过主库远程配置")
        return
    try:
        if not is_git_repo(repo_root):
            if url or branch:
                subprocess.run(["git", "init"], check=True, cwd=repo_root)

        if url and remote:
            # 检查 remote 是否存在
            r = subprocess.run(
                ["git", "remote", "get-url", remote],
                cwd=repo_root,
                capture_output=True,
                text=True,
            )
            if r.returncode != 0:
                subprocess.run(
                    ["git", "remote", "add", remote, url],
                    check=True,
                    cwd=repo_root,
                )
            else:
                subprocess.run(
                    ["git", "remote", "set-url", remote, url],
                    check=True,
                    cwd=repo_root,
                )

            log.info("正在从 %s 获取更新...", remote)
            subprocess.run(["git", "fetch", remote], check=True, cwd=repo_root)

        if branch:
            # 切换到指定分支
            # 如果是新仓库，尝试从远程分支创建
            try:
                subprocess.run(
                    [
                        "git",
                        "checkout",
                        "-B",
                        branch,
                        f"{remote}/{branch}" if remote else "",
                    ],
                    check=True,
                    cwd=repo_root,
                    stderr=subprocess.DEVNULL,
                )
            except subprocess.CalledProcessError:
                # 如果远程没有该分支或没有 remote，则直接 checkout -B
                subprocess.run(
                    ["git", "checkout", "-B", branch],
                    check=True,
                    cwd=repo_root,
                )

    except subprocess.CalledProcessError as e:
        log.error("主库 Git 配置失败: %s", e)


def ensure_initial_commit(repo_root: Path):
    """主库无提交时先提交 .gitignore，否则 git submodule add 会报 branch yet to be born。"""
    r = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
    )
    if r.returncode != 0:
        gitignore = repo_root / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("# Rime updator\n", encoding="utf-8")
        subprocess.run(["git", "add", ".gitignore"], check=True, cwd=repo_root)
        subprocess.run(
            ["git", "commit", "-m", "chore: initial commit with .gitignore"],
            check=True,
            cwd=repo_root,
        )
        log.info("已创建初始提交（.gitignore）")


def _normalize_gitignore_entry(entry: str) -> str:
    """统一去掉首部 './'，便于与 .gitignore 中已有行比较。"""
    s = entry.strip()
    return s[2:].lstrip("/") if s.startswith("./") else s


def ensure_gitignore_entry(entry: str, gitignore: Path):
    """将路径加入 .gitignore（若尚未存在）。"""
    normalized = _normalize_gitignore_entry(entry)
    if not gitignore.exists():
        gitignore.write_text(normalized + "\n", encoding="utf-8")
        return
    text = gitignore.read_text(encoding="utf-8")
    for line in text.splitlines():
        if _normalize_gitignore_entry(line) == normalized:
            return
    with open(gitignore, "a", encoding="utf-8") as f:
        f.write("\n" + normalized + "\n")


def git_commit_if_changed(repo_root: Path, message: str):
    """如果主库有暂存的更改，则执行 commit。"""
    try:
        # 检查是否有暂存的更改
        r = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=repo_root)
        if r.returncode != 0:
            subprocess.run(["git", "commit", "-m", message], check=True, cwd=repo_root)
            print(f"已自动提交: {message}")
    except subprocess.CalledProcessError as e:
        print(f"自动提交失败: {e}", file=sys.stderr)


def _submodule_ensure_branch(path: Path, branch: str, name: str, repo_root: Path):
    """子模块内：若远端有对应分支则 checkout，否则新建分支并推送。"""
    subprocess.run(
        ["git", "-C", str(path), "fetch", "origin"],
        check=False,
        cwd=repo_root,
        stderr=subprocess.DEVNULL,
    )
    r = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--verify", f"origin/{branch}"],
        cwd=repo_root,
        capture_output=True,
    )
    if r.returncode == 0:
        subprocess.run(
            ["git", "-C", str(path), "checkout", "-B", branch, f"origin/{branch}"],
            check=True,
            cwd=repo_root,
        )
    else:
        log.info("子模块 %s: 远程无分支 %s，新建并推送", name, branch)
        subprocess.run(
            ["git", "-C", str(path), "checkout", "-B", branch],
            check=True,
            cwd=repo_root,
        )
        subprocess.run(
            ["git", "-C", str(path), "push", "-u", "origin", branch],
            check=False,
            cwd=repo_root,
        )


def init_submodule(cfg: dict, repo_root: Path):
    """初始化子模块：分支 → 更新 .gitignore → commit/push → 主库 add。"""
    path_str = cfg["path"].strip()
    path = repo_root / path_str.lstrip("./")
    url = cfg["url"].strip()
    branch = cfg.get("branch", "main")
    name = cfg.get("name") or cfg.get("path")
    ignore_list = cfg.get("ignore") or []

    try:
        if path.exists() and (path / ".git").exists():
            # 已存在子模块或独立 git 目录
            _submodule_ensure_branch(path, branch, name, repo_root)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            log.info("正在添加子模块 %s...", name)
            r = subprocess.run(
                ["git", "submodule", "add", "-f", "-b", branch, url, str(path)],
                check=False,
                cwd=repo_root,
                stderr=subprocess.DEVNULL,
            )
            if r.returncode != 0:
                log.info("远程不存在分支 %s，将创建该分支后再加入主库", branch)
                rel = path.relative_to(repo_root)
                rel_str = rel.as_posix()
                subprocess.run(
                    ["git", "submodule", "deinit", "-f", rel_str],
                    cwd=repo_root,
                    check=False,
                    capture_output=True,
                )
                subprocess.run(
                    ["git", "rm", "-f", rel_str],
                    cwd=repo_root,
                    check=False,
                    capture_output=True,
                )
                subprocess.run(
                    ["git", "config", "--remove-section", f"submodule.{rel_str}"],
                    cwd=repo_root,
                    check=False,
                    capture_output=True,
                )
                gitmodules = repo_root / ".gitmodules"
                if gitmodules.exists():
                    subprocess.run(
                        [
                            "git",
                            "config",
                            "-f",
                            str(gitmodules),
                            "--remove-section",
                            f"submodule.{rel_str}",
                        ],
                        cwd=repo_root,
                        check=False,
                        capture_output=True,
                    )
                if path.exists():
                    shutil.rmtree(path)
                modules_dir = repo_root / ".git" / "modules" / rel_str
                if modules_dir.exists():
                    shutil.rmtree(modules_dir)
                subprocess.run(
                    ["git", "submodule", "add", "-f", url, path_str],
                    check=True,
                    cwd=repo_root,
                )
                _submodule_ensure_branch(path, branch, name, repo_root)

        # 2. 更新该子模块的 .gitignore
        gitignore = path / ".gitignore"
        for pattern in ignore_list:
            ensure_gitignore_entry(pattern.strip(), gitignore)
        subprocess.run(
            ["git", "-C", str(path), "add", ".gitignore"],
            check=False,
            cwd=repo_root,
        )
        r = subprocess.run(
            ["git", "-C", str(path), "diff", "--cached", "--quiet"],
            cwd=repo_root,
        )
        if r.returncode != 0:
            subprocess.run(
                ["git", "-C", str(path), "commit", "-m", "chore: update gitignore"],
                check=False,
                cwd=repo_root,
            )
            subprocess.run(
                ["git", "-C", str(path), "push"],
                check=False,
                cwd=repo_root,
            )

        # 3. 主库目录内将该子模块加入（新加或更新指针）
        subprocess.run(
            ["git", "add", str(path.relative_to(repo_root))],
            check=False,
            cwd=repo_root,
        )
        if (repo_root / ".gitmodules").exists():
            subprocess.run(
                ["git", "add", ".gitmodules"],
                check=False,
                cwd=repo_root,
            )

    except subprocess.CalledProcessError as e:
        log.error("子模块 %s 初始化失败: %s", name, e)


def init_symlink(cfg: dict, repo_root: Path):
    path_str = cfg["path"].strip()
    path = repo_root / path_str.lstrip("./")
    target = cfg["url"].strip()
    name = cfg.get("name") or cfg.get("path")
    ignore_list = cfg.get("ignore") or []

    # 处理 ignore 配置，添加到 .gitignore
    gitignore = repo_root / ".gitignore"
    for pattern in ignore_list:
        ensure_gitignore_entry(os.path.join(path_str, pattern), gitignore)

    if path.exists():
        if path.is_symlink() and os.path.realpath(path) == os.path.realpath(target):
            return
        print(f"路径已存在且非预期软链接，跳过: {path}", file=sys.stderr)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target, path)

    # 2. 处理 is-ignored 配置
    if cfg.get("is-ignored", False):
        gitignore = repo_root / ".gitignore"
        ensure_gitignore_entry(str(path_str), gitignore)

    subprocess.run(["git", "add", str(path.relative_to(repo_root))], check=False, cwd=repo_root)
    subprocess.run(["git", "add", ".gitignore"], check=False, cwd=repo_root)


def pull_main_repo(repo_root: Path, init_cfg: dict):
    """主库已有提交且配置了 remote 时执行 pull，支持重复执行时先拉再合并。"""
    r = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
    )
    if r.returncode != 0:
        return
    remote = (init_cfg.get("git-remote") or "origin").strip()
    url_r = subprocess.run(
        ["git", "remote", "get-url", remote],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if url_r.returncode != 0:
        return
    log.info("主库拉取更新: git pull")
    subprocess.run(["git", "pull"], check=False, cwd=repo_root)


def main():
    repo_root = find_repo_root()
    os.chdir(repo_root)
    config = load_config(repo_root)
    init_cfg = config.get("init") or {}
    init_main_repo(init_cfg, repo_root)
    ensure_initial_commit(repo_root)
    subdirs = config.get("subdir") or []
    for item in subdirs:
        name = item.get("name", "")
        path = item.get("path", "")
        typ = (item.get("type") or "gitsubmodule").strip().lower()
        if not name or not path:
            continue
        if typ == "gitsubmodule":
            init_submodule(item, repo_root)
        elif typ == "ln":
            init_symlink(item, repo_root)
        else:
            log.warning("未知 type=%s，跳过: %s", typ, name)
    subprocess.run(["git", "add", "-A"], check=False, cwd=repo_root)
    git_commit_if_changed(repo_root, "chore: init submodules and gitignore")
    # 主库 push（若有 remote）
    # 先 pull 再合并
    pull_main_repo(repo_root, init_cfg)
    remote = (init_cfg.get("git-remote") or "origin").strip()
    branch = (init_cfg.get("git-branch") or "main").strip() or "main"
    r = subprocess.run(
        ["git", "remote", "get-url", remote],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if r.returncode == 0:
        subprocess.run(
            ["git", "push", "-u", remote, branch],
            check=False,
            cwd=repo_root,
        )


if __name__ == "__main__":
    main()
