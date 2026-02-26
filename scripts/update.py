#!/usr/bin/env python3
"""根据 init.yaml 解压更新子模块并合并到目标目录。支持 unzip-file 解压去壳、子模块 add/commit，合并时按各 subdir 的 ignore/symlink 配置。"""
import argparse
import fnmatch
import logging
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("update")


def find_repo_root() -> Path:
    """优先从当前目录及父目录查找含 init.yaml 的仓库根，否则用脚本所在仓库。"""
    for d in [Path.cwd()] + list(Path.cwd().resolve().parents):
        if (d / "init.yaml").exists():
            return d
    return SCRIPT_DIR.parent


def load_config(repo_root: Path):
    init_yaml = repo_root / "init.yaml"
    if not init_yaml.exists():
        print(f"未找到 {init_yaml}", file=sys.stderr)
        sys.exit(1)
    with open(init_yaml, encoding="utf-8") as f:
        return yaml.safe_load(f)


def should_ignore(name: str, ignore_patterns: list) -> bool:
    if not ignore_patterns:
        return False
    for pat in ignore_patterns:
        if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(name, os.path.join("*", pat)):
            return True
    return False


def unzip_and_flatten(zip_path: Path, dest_path: Path):
    """解压 zip 到 dest_path；若仅有一个顶层目录则去壳（将其内容移到 dest_path）。"""
    dest_path.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        top_level = set()
        for n in names:
            part = n.rstrip("/").split("/")[0]
            if part:
                top_level.add(part)
        if len(top_level) == 1:
            inner = list(top_level)[0]
            zf.extractall(dest_path)
            inner_path = dest_path / inner
            if inner_path.is_dir():
                for x in list(inner_path.iterdir()):
                    shutil.move(str(x), str(dest_path))
                inner_path.rmdir()
        else:
            zf.extractall(dest_path)


def unzip_and_commit_subdir(item: dict, repo_root: Path):
    """若 subdir 配置了 unzip-file，从主库根解压到子模块、去壳，并子模块内 add、commit。"""
    unzip_name = (item.get("unzip-file") or "").strip()
    if not unzip_name:
        return
    zip_path = repo_root / unzip_name
    if not zip_path.exists():
        log.warning("unzip-file 不存在，跳过: %s", zip_path)
        return
    path_str = item["path"].strip().lstrip("./")
    dest_path = repo_root / path_str
    name = item.get("name", path_str)
    if not (dest_path / ".git").exists():
        log.warning("子模块非 git 目录，跳过解压: %s", dest_path)
        return
    log.info("解压 %s 到子模块 %s 并去壳", unzip_name, name)
    unzip_and_flatten(zip_path, dest_path)
    subprocess.run(["git", "-C", str(dest_path), "add", "-A"], check=False, cwd=repo_root)
    r = subprocess.run(
        ["git", "-C", str(dest_path), "diff", "--cached", "--quiet"],
        cwd=repo_root,
        capture_output=True,
    )
    if r.returncode != 0:
        subprocess.run(
            ["git", "-C", str(dest_path), "commit", "-m", "chore: update from unzip"],
            check=False,
            cwd=repo_root,
        )
        log.info("子模块 %s 已提交", name)


def copy_tree(
    src: Path,
    dst: Path,
    ignore_patterns: list,
    symlink_entries: list,
    rel_prefix: str = "",
):
    """拷贝目录树，忽略指定模式与软链接项。"""
    dst.mkdir(parents=True, exist_ok=True)
    for entry in src.iterdir():
        name = entry.name
        rel = f"{rel_prefix}/{name}" if rel_prefix else name
        if should_ignore(name, ignore_patterns) or rel in symlink_entries:
            continue
        d = dst / name
        if entry.is_dir():
            copy_tree(entry, d, ignore_patterns, symlink_entries, rel)
        elif entry.is_file():
            try:
                if d.resolve() != entry.resolve():
                    shutil.copy2(entry, d)
            except OSError:
                pass


def apply_subdir(item: dict, target_root: Path, repo_root: Path):
    """将子模块的**内容**合并到 target_root（不创建以子模块名为名的子文件夹）。"""
    typ = (item.get("type") or "gitsubmodule").strip().lower()
    if typ == "ln":
        return  # 软链接项指向 target，无需拷贝，避免 SameFileError
    path = repo_root / item["path"].strip().lstrip("./")
    ignore_patterns = item.get("ignore") or []
    symlink_entries = item.get("symlink") or []
    if not path.exists():
        log.warning("源路径不存在，跳过: %s", path)
        return
    if path.is_file():
        target_root.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target_root / path.name)
        return
    symlink_entries = [s.strip().lstrip("/") for s in symlink_entries if s]
    copy_tree(path, target_root, ignore_patterns, symlink_entries, "")
    for rel in symlink_entries:
        src_item = (path / rel).resolve()
        dst_item = target_root / rel
        if not src_item.exists():
            log.warning("软链接源不存在，跳过: %s", src_item)
            continue
        if dst_item.exists():
            dst_item.unlink()
        dst_item.parent.mkdir(parents=True, exist_ok=True)
        try:
            dst_item.symlink_to(src_item)
        except OSError as e:
            log.warning("创建软链接失败 %s -> %s: %s", dst_item, src_item, e)


def backup_target(target_root: Path, backup_folder: Path):
    """将目标目录备份到 backup_folder/<timestamp>。"""
    if not target_root.exists() or not any(target_root.iterdir()):
        return
    backup_folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dest = backup_folder / stamp
    shutil.copytree(target_root, backup_dest, dirs_exist_ok=True)
    log.info("已备份到 %s", backup_dest)


def clear_target_contents(target_root: Path):
    """删除目标文件夹内所有内容（保留目录本身）。"""
    if not target_root.exists():
        return
    for child in list(target_root.iterdir()):
        if child.is_file() or child.is_symlink():
            child.unlink()
        else:
            shutil.rmtree(child)
    log.info("已清空目标目录 %s", target_root)


def push_submodule(path: Path, name: str, repo_root: Path):
    """子模块内 add、commit（有变更时）、push。"""
    if not (path / ".git").exists():
        return
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=False, cwd=repo_root)
    r = subprocess.run(
        ["git", "-C", str(path), "diff", "--cached", "--quiet"],
        cwd=repo_root,
        capture_output=True,
    )
    if r.returncode != 0:
        subprocess.run(
            ["git", "-C", str(path), "commit", "-m", "chore: update"],
            check=False,
            cwd=repo_root,
        )
    r = subprocess.run(
        ["git", "-C", str(path), "push"],
        check=False,
        cwd=repo_root,
        capture_output=True,
    )
    if r.returncode == 0:
        log.info("子模块 %s 已 push", name)
    else:
        log.warning("子模块 %s push 失败或无可推送", name)


def push_main_repo(repo_root: Path, init_cfg: dict):
    """主库 add、commit（有变更时）、push。"""
    subprocess.run(["git", "add", "-A"], check=False, cwd=repo_root)
    r = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=repo_root,
        capture_output=True,
    )
    if r.returncode != 0:
        subprocess.run(
            ["git", "commit", "-m", "chore: update submodules and config"],
            check=False,
            cwd=repo_root,
        )
    remote = (init_cfg.get("git-remote") or "origin").strip()
    branch = (init_cfg.get("git-branch") or "main").strip() or "main"
    r = subprocess.run(
        ["git", "remote", "get-url", remote],
        cwd=repo_root,
        capture_output=True,
    )
    if r.returncode == 0:
        subprocess.run(
            ["git", "push", remote, branch],
            check=False,
            cwd=repo_root,
        )
        log.info("主库已 push %s %s", remote, branch)


def main():
    repo_root = find_repo_root()
    parser = argparse.ArgumentParser(description="解压更新子模块并合并到目标目录")
    parser.add_argument("--target", "-t", help="目标目录（未指定时使用 init.target-folder）")
    parser.add_argument("--no-backup", action="store_true", help="跳过备份")
    parser.add_argument("-u", "--unzip", action="store_true", help="解压配置的 zip 到对应子模块，找不到 zip 则跳过")
    parser.add_argument("-p", "--push", action="store_true", help="拷贝完成后执行各子模块与主库的 add/commit/push")
    args = parser.parse_args()
    os.chdir(repo_root)
    config = load_config(repo_root)
    init_cfg = config.get("init") or {}
    target_root = (args.target or (init_cfg.get("target-folder") or "").strip())
    if not target_root:
        log.error("未指定 --target 且 init.target-folder 未配置")
        sys.exit(1)
    target_root = Path(target_root).resolve()
    subdirs = config.get("subdir") or []
    if args.unzip:
        for item in subdirs:
            unzip_and_commit_subdir(item, repo_root)
    if not args.no_backup:
        backup_path = (init_cfg.get("backup-folder") or "").strip().lstrip("./")
        if backup_path:
            backup_target(target_root, (repo_root / backup_path).resolve())
    clear_target_contents(target_root)
    target_root.mkdir(parents=True, exist_ok=True)
    for item in subdirs:
        apply_subdir(item, target_root, repo_root)
    if args.push:
        for item in subdirs:
            typ = (item.get("type") or "gitsubmodule").strip().lower()
            if typ != "gitsubmodule":
                continue
            path = repo_root / item["path"].strip().lstrip("./")
            name = item.get("name", path.name)
            if path.exists():
                push_submodule(path, name, repo_root)
        push_main_repo(repo_root, init_cfg)


if __name__ == "__main__":
    main()
