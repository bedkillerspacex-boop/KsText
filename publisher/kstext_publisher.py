#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, Queue
from tkinter import (
    BOTH,
    END,
    HORIZONTAL,
    LEFT,
    NONE,
    RIGHT,
    VERTICAL,
    W,
    BooleanVar,
    DoubleVar,
    PanedWindow,
    StringVar,
    Tk,
    messagebox,
    simpledialog,
    ttk,
)
from tkinter.scrolledtext import ScrolledText
from urllib.parse import quote


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_REPO_CACHE_ROOT = SCRIPT_DIR / "repo_cache"
DEFAULT_OWNER_REPO = "bedkillerspacex-boop/KsText"
DEFAULT_BRANCH = "master"
DEFAULT_TAGS = ["killsay", "community"]
DEFAULT_SERVER_TAGS = ["generic"]
PUSH_PROGRESS_RE = re.compile(
    r"(?P<stage>Enumerating objects|Counting objects|Compressing objects|Writing objects):\s*"
    r"(?P<percent>\d+)%\s*\((?P<done>\d+)/(?P<total>\d+)\)"
    r"(?:,\s*(?P<size>[\d.]+)\s*(?P<size_unit>[KMGT]?i?B|[KMGT]?B))?",
    re.IGNORECASE,
)


class PublishError(RuntimeError):
    pass


@dataclass
class BuildStats:
    total_packs: int
    changed_packs: int
    new_packs: int
    unchanged_packs: int


@dataclass
class BuildResult:
    index_data: dict
    stats: BuildStats
    warnings: list[str]


@dataclass
class PackDocument:
    path: Path
    schema_version: int
    pack_id: str
    name: str
    author: str
    summary: str
    language: str
    tags: list[str]
    server_tags: list[str]
    entries: list[str]
    file_version: int
    file_updated_at: str


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean_string(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def clean_list(value: object, default: list[str]) -> list[str]:
    if not isinstance(value, list):
        return list(default)
    result: list[str] = []
    for item in value:
        text = clean_string(item)
        if text:
            result.append(text)
    return result or list(default)


def clean_entries(value: object, pack_name: str) -> list[str]:
    if not isinstance(value, list):
        raise PublishError(f"{pack_name}: entries 不是数组")
    result: list[str] = []
    for item in value:
        text = clean_string(item)
        if text:
            result.append(text)
    if not result:
        raise PublishError(f"{pack_name}: entries 为空")
    return result


def as_int(value: object, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise PublishError(f"JSON 解析失败: {path.name}: {exc}") from exc


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def json_bytes(data: dict) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def size_to_bytes(value_text: str, unit_text: str) -> float:
    value = float(value_text)
    unit = unit_text.upper()
    factors = {
        "B": 1,
        "KB": 1000,
        "MB": 1000**2,
        "GB": 1000**3,
        "TB": 1000**4,
        "KIB": 1024,
        "MIB": 1024**2,
        "GIB": 1024**3,
        "TIB": 1024**4,
    }
    return value * factors.get(unit, 1)


def format_size_text(size_bytes: float | None) -> str:
    if size_bytes is None:
        return ""
    if size_bytes >= 1024**3:
        return f"{size_bytes / (1024**3):.2f} GB"
    if size_bytes >= 1024**2:
        return f"{size_bytes / (1024**2):.2f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes:.0f} B"


def parse_push_progress(text: str) -> tuple[float | None, str] | None:
    match = PUSH_PROGRESS_RE.search(text)
    if not match:
        return None
    stage = match.group("stage")
    percent = float(match.group("percent"))
    done = int(match.group("done"))
    total = int(match.group("total"))
    status = f"{stage} {int(percent)}% ({done}/{total})"
    size_text = match.group("size")
    size_unit = match.group("size_unit")
    if size_text and size_unit:
        transferred = size_to_bytes(size_text, size_unit)
        estimated_total = transferred if percent <= 0 else transferred / max(percent / 100.0, 0.01)
        status = f"{stage} {int(percent)}% {format_size_text(transferred)}/{format_size_text(estimated_total)}"
    return percent, status


def normalize_owner_repo(value: str) -> str:
    text = value.strip()
    if not text:
        return DEFAULT_OWNER_REPO
    if text.startswith("https://github.com/"):
        text = text[len("https://github.com/") :]
    elif text.startswith("http://github.com/"):
        text = text[len("http://github.com/") :]
    if text.endswith(".git"):
        text = text[:-4]
    return text.strip("/\\") or DEFAULT_OWNER_REPO


def cache_dir_for_repo(owner_repo: str) -> Path:
    normalized = normalize_owner_repo(owner_repo)
    safe_name = normalized.replace("/", "__").replace("\\", "__").replace(":", "_")
    return DEFAULT_REPO_CACHE_ROOT / safe_name


def default_remote_url(owner_repo: str) -> str:
    return f"https://github.com/{normalize_owner_repo(owner_repo)}.git"


def target_display(owner_repo: str, branch: str, repo_dir: Path) -> str:
    return f"{normalize_owner_repo(owner_repo)}@{clean_string(branch) or DEFAULT_BRANCH} -> {repo_dir}"


def ensure_repo_dir(path_text: str) -> Path:
    repo_dir = Path(path_text).expanduser()
    if not repo_dir.exists():
        raise PublishError(f"仓库目录不存在: {repo_dir}")
    if not (repo_dir / ".git").exists():
        raise PublishError(f"这不是 git 仓库: {repo_dir}")
    return repo_dir.resolve()


def run_git(repo_dir: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if process.returncode != 0:
        message = process.stderr.strip() or process.stdout.strip() or "未知 git 错误"
        raise PublishError(f"git {' '.join(args)} 失败: {message}")
    return process.stdout


def git_status(repo_dir: Path) -> str:
    return run_git(repo_dir, "status", "--short").strip()


def infer_owner_repo(repo_dir: Path) -> str:
    try:
        output = run_git(repo_dir, "config", "--get", "remote.origin.url").strip()
    except PublishError:
        return DEFAULT_OWNER_REPO
    return normalize_owner_repo(output)


def infer_branch(repo_dir: Path) -> str:
    try:
        branch = run_git(repo_dir, "branch", "--show-current").strip()
    except PublishError:
        return DEFAULT_BRANCH
    return branch or DEFAULT_BRANCH


def infer_github_login_status() -> str:
    try:
        process = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "未检测到 gh"

    output = ((process.stdout or "") + "\n" + (process.stderr or "")).strip()
    if process.returncode != 0:
        lowered = output.lower()
        if "not logged" in lowered or "not logged into any hosts" in lowered:
            return "未登录 GitHub"
        return "gh 未登录或状态未知"

    for line in output.splitlines():
        stripped = line.strip()
        if "Logged in to github.com account" in stripped:
            return f"github.com: {stripped.split('account', 1)[-1].strip()}"
        if stripped.startswith("account "):
            return f"github.com: {stripped[len('account '):].strip()}"
    return "已登录 GitHub"


def sync_cached_repo(
    repo_dir: Path,
    owner_repo: str,
    branch: str,
    progress_callback=None,
    output_callback=None,
) -> str:
    remote_url = default_remote_url(owner_repo)
    repo_dir.parent.mkdir(parents=True, exist_ok=True)

    if not repo_dir.exists():
        if progress_callback:
            progress_callback(10.0, f"正在克隆 {owner_repo}")
        process = subprocess.run(
            ["git", "clone", "--branch", branch, remote_url, str(repo_dir)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if process.returncode != 0:
            message = process.stderr.strip() or process.stdout.strip() or "未知 clone 错误"
            raise PublishError(f"缓存仓库克隆失败: {message}")
        if output_callback:
            output_callback(process.stdout.strip() or f"已克隆缓存仓库: {repo_dir}")
        if progress_callback:
            progress_callback(100.0, f"已克隆 {owner_repo}")
        return f"已克隆缓存仓库: {repo_dir}"

    if not (repo_dir / ".git").exists():
        raise PublishError(f"缓存目录存在但不是 git 仓库: {repo_dir}")

    local_changes = git_status(repo_dir)
    if progress_callback:
        progress_callback(20.0, f"正在获取 {owner_repo}")
    run_git(repo_dir, "remote", "set-url", "origin", remote_url)
    run_git(repo_dir, "fetch", "origin", branch)

    current_branch = infer_branch(repo_dir)
    if current_branch != branch and not local_changes:
        if progress_callback:
            progress_callback(45.0, f"正在切换到 {branch}")
        run_git(repo_dir, "checkout", branch)

    if local_changes:
        if progress_callback:
            progress_callback(100.0, "检测到本地改动，跳过自动更新")
        return f"缓存仓库有本地改动，已跳过自动更新: {repo_dir}"

    if progress_callback:
        progress_callback(70.0, f"正在拉取 {branch}")
    run_git(repo_dir, "pull", "--ff-only", "origin", branch)
    if progress_callback:
        progress_callback(100.0, f"同步完成 {owner_repo}")
    return f"已更新缓存仓库: {repo_dir}"


def raw_download_url(owner_repo: str, branch: str, file_name: str) -> str:
    return f"https://raw.githubusercontent.com/{owner_repo}/{branch}/packs/{quote(file_name)}"


def load_existing_index(index_path: Path) -> dict[str, dict]:
    if not index_path.exists():
        return {}
    data = read_json(index_path)
    packs = data.get("packs")
    if not isinstance(packs, list):
        return {}
    result: dict[str, dict] = {}
    for pack in packs:
        if isinstance(pack, dict):
            pack_id = clean_string(pack.get("id"))
            if pack_id:
                result[pack_id] = pack
    return result


def collect_pack_files(repo_dir: Path) -> list[Path]:
    packs_dir = repo_dir / "packs"
    if not packs_dir.is_dir():
        raise PublishError(f"缺少目录: {packs_dir}")
    files = [path for path in packs_dir.glob("*.json") if path.is_file()]
    files.sort(key=lambda item: item.name.lower())
    return files


def validate_pack_file_stem(file_stem: str) -> str:
    text = clean_string(file_stem)
    if not text:
        raise PublishError("文件名不能为空")
    if any(char in text for char in ("/", "\\")):
        raise PublishError("文件名不能包含路径分隔符")
    if text in {".", ".."}:
        raise PublishError("文件名不合法")
    file_name = text if text.lower().endswith(".json") else f"{text}.json"
    if Path(file_name).name != file_name:
        raise PublishError("文件名不合法")
    return text


def new_pack_document(repo_dir: Path, file_stem: str, display_name: str) -> PackDocument:
    file_stem = validate_pack_file_stem(file_stem)
    file_name = file_stem if file_stem.lower().endswith(".json") else f"{file_stem}.json"
    path = repo_dir / "packs" / file_name
    if path.exists():
        raise PublishError(f"文件已存在: {path.name}")
    return PackDocument(
        path=path,
        schema_version=1,
        pack_id=file_stem.removesuffix(".json"),
        name=display_name or file_stem,
        author="佚名",
        summary=display_name or file_stem,
        language="zh-CN",
        tags=list(DEFAULT_TAGS),
        server_tags=list(DEFAULT_SERVER_TAGS),
        entries=["{name}"],
        file_version=1,
        file_updated_at=utc_now(),
    )


def load_pack_documents(repo_dir: Path) -> tuple[list[PackDocument], dict[str, dict], list[str]]:
    existing_by_id = load_existing_index(repo_dir / "index.json")
    documents: list[PackDocument] = []
    warnings: list[str] = []
    for pack_file in collect_pack_files(repo_dir):
        data = read_json(pack_file)
        file_id = clean_string(data.get("id")) or pack_file.stem
        existing = existing_by_id.get(file_id, {})
        source_author = clean_string(data.get("author"))
        existing_author = clean_string(existing.get("author"))
        doc = PackDocument(
            path=pack_file,
            schema_version=as_int(data.get("schemaVersion"), 1),
            pack_id=file_id,
            name=clean_string(data.get("name")) or clean_string(existing.get("name")) or pack_file.stem,
            author=source_author or existing_author or "佚名",
            summary=(
                clean_string(data.get("summary"))
                or clean_string(data.get("description"))
                or clean_string(existing.get("summary"))
                or pack_file.stem
            ),
            language=clean_string(data.get("language")) or clean_string(existing.get("language")) or "zh-CN",
            tags=clean_list(data.get("tags"), clean_list(existing.get("tags"), DEFAULT_TAGS)),
            server_tags=clean_list(data.get("serverTags"), clean_list(existing.get("serverTags"), DEFAULT_SERVER_TAGS)),
            entries=clean_entries(data.get("entries"), pack_file.name),
            file_version=as_int(data.get("version"), as_int(existing.get("version"), 1)),
            file_updated_at=clean_string(data.get("updatedAt")) or clean_string(existing.get("updatedAt")),
        )
        documents.append(doc)
        if not source_author and not existing_author:
            warnings.append(f"{pack_file.name}: 作者为空，已自动写成 佚名")
    return documents, existing_by_id, warnings


def normalize_pack_document(doc: PackDocument) -> None:
    doc.pack_id = clean_string(doc.pack_id) or doc.path.stem
    doc.name = clean_string(doc.name) or doc.path.stem
    doc.author = clean_string(doc.author) or "佚名"
    doc.summary = clean_string(doc.summary) or doc.name
    doc.language = clean_string(doc.language) or "zh-CN"
    doc.tags = clean_list(doc.tags, DEFAULT_TAGS)
    doc.server_tags = clean_list(doc.server_tags, DEFAULT_SERVER_TAGS)
    doc.entries = [line.strip() for line in doc.entries if clean_string(line)]
    if doc.file_version <= 0:
        doc.file_version = 1
    if not doc.entries:
        raise PublishError(f"{doc.path.name}: entries 为空")


def pack_payload(doc: PackDocument) -> dict:
    normalize_pack_document(doc)
    return {
        "schemaVersion": max(doc.schema_version, 1),
        "id": doc.pack_id,
        "name": doc.name,
        "author": doc.author,
        "version": max(doc.file_version, 1),
        "updatedAt": doc.file_updated_at or utc_now(),
        "description": doc.summary,
        "language": doc.language,
        "tags": doc.tags,
        "serverTags": doc.server_tags,
        "entries": doc.entries,
    }


def save_pack_document(doc: PackDocument) -> None:
    payload = pack_payload(doc)
    doc.file_version = as_int(payload.get("version"), 1)
    doc.file_updated_at = clean_string(payload.get("updatedAt"))
    doc.path.write_bytes(json_bytes(payload))


def save_all_pack_documents(documents: list[PackDocument]) -> None:
    for doc in documents:
        save_pack_document(doc)


def build_index_from_documents(
    owner_repo: str,
    branch: str,
    documents: list[PackDocument],
    existing_by_id: dict[str, dict],
    base_warnings: list[str],
    bump_changed_version: bool,
) -> BuildResult:
    generated_at = utc_now()
    seen_ids: set[str] = set()
    packs: list[dict] = []
    warnings = list(base_warnings)
    changed_packs = 0
    new_packs = 0
    unchanged_packs = 0

    for doc in documents:
        normalize_pack_document(doc)
        if doc.pack_id in seen_ids:
            raise PublishError(f"存在重复 ID: {doc.pack_id}")
        seen_ids.add(doc.pack_id)

        existing = existing_by_id.get(doc.pack_id, {})
        initial_payload = pack_payload(doc)
        initial_sha256 = sha256_bytes(json_bytes(initial_payload))
        old_sha256 = clean_string(existing.get("sha256"))
        is_new = not existing
        is_changed = is_new or initial_sha256 != old_sha256

        existing_version = as_int(existing.get("version"), 0)
        base_version = max(doc.file_version, existing_version, 1)
        if is_new:
            version = base_version
        elif is_changed and bump_changed_version:
            version = base_version + 1
        else:
            version = base_version

        updated_at = generated_at if is_changed else (doc.file_updated_at or clean_string(existing.get("updatedAt")) or generated_at)
        doc.file_version = version
        doc.file_updated_at = updated_at
        final_payload = pack_payload(doc)
        final_sha256 = sha256_bytes(json_bytes(final_payload))

        packs.append(
            {
                "id": doc.pack_id,
                "name": doc.name,
                "author": doc.author,
                "summary": doc.summary,
                "language": doc.language,
                "tags": doc.tags,
                "serverTags": doc.server_tags,
                "version": version,
                "updatedAt": updated_at,
                "entryCount": len(doc.entries),
                "sha256": final_sha256,
                "downloadUrl": raw_download_url(owner_repo, branch, doc.path.name),
            }
        )

        if is_new:
            new_packs += 1
        elif is_changed:
            changed_packs += 1
        else:
            unchanged_packs += 1

    packs.sort(key=lambda item: item["name"].lower())
    return BuildResult(
        index_data={"schemaVersion": 1, "generatedAt": generated_at, "packs": packs},
        stats=BuildStats(len(packs), changed_packs, new_packs, unchanged_packs),
        warnings=warnings,
    )


def prepare_publish_assets(
    repo_dir: Path,
    owner_repo: str,
    branch: str,
    bump_changed_version: bool,
) -> tuple[list[PackDocument], dict[str, dict], list[str], BuildResult]:
    documents, existing_by_id, warnings = load_pack_documents(repo_dir)
    result = build_index_from_documents(owner_repo, branch, documents, existing_by_id, warnings, bump_changed_version)
    save_all_pack_documents(documents)
    return documents, existing_by_id, warnings, result


def write_index_file(repo_dir: Path, result: BuildResult) -> Path:
    index_path = repo_dir / "index.json"
    write_json(index_path, result.index_data)
    return index_path


def run_git_push_with_progress(repo_dir: Path, branch: str, progress_callback=None, output_callback=None) -> str:
    process = subprocess.Popen(
        ["git", "-C", str(repo_dir), "push", "--progress", "origin", branch],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    if process.stdout is None:
        raise PublishError("无法读取 git push 输出")

    chunks: list[str] = []
    current: list[str] = []
    while True:
        char = process.stdout.read(1)
        if not char:
            break
        if char in ("\r", "\n"):
            if current:
                line = "".join(current).strip()
                current.clear()
                if line:
                    chunks.append(line)
                    if output_callback:
                        output_callback(line)
                    if progress_callback:
                        progress = parse_push_progress(line)
                        if progress:
                            progress_callback(*progress)
            continue
        current.append(char)

    if current:
        line = "".join(current).strip()
        if line:
            chunks.append(line)
            if output_callback:
                output_callback(line)
            if progress_callback:
                progress = parse_push_progress(line)
                if progress:
                    progress_callback(*progress)

    output_text = "\n".join(chunks).strip()
    if process.wait() != 0:
        raise PublishError(output_text or "git push 失败")
    return output_text


def build_summary(result: BuildResult) -> str:
    stats = result.stats
    return f"共 {stats.total_packs} 个包 | 新增 {stats.new_packs} | 变化 {stats.changed_packs} | 未变 {stats.unchanged_packs}"


def run_cli(args: argparse.Namespace) -> int:
    owner_repo = normalize_owner_repo(args.owner_repo or DEFAULT_OWNER_REPO)
    branch = args.branch or DEFAULT_BRANCH
    repo_dir = Path(args.repo).expanduser() if args.repo else cache_dir_for_repo(owner_repo)
    sync_cached_repo(repo_dir, owner_repo, branch)
    repo_dir = ensure_repo_dir(str(repo_dir))
    documents, _, _, result = prepare_publish_assets(repo_dir, owner_repo, branch, not args.no_bump_version)
    print(build_summary(result))
    for warning in result.warnings:
        print(f"[warn] {warning}")
    if args.dry_run and not args.write_index and not args.commit and not args.push:
        return 0
    if args.write_index or args.commit or args.push:
        save_all_pack_documents(documents)
        print(f"已写入 {write_index_file(repo_dir, result)}")
    if args.commit or args.push:
        run_git(repo_dir, "add", "index.json", "packs")
        if git_status(repo_dir):
            print(run_git(repo_dir, "commit", "-m", args.message or "update KsText packs").strip())
            if args.push:
                print(run_git_push_with_progress(repo_dir, infer_branch(repo_dir)))
        else:
            print("没有可提交的改动")
    return 0


class PublisherApp(Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("KsText Publisher")
        self.geometry("1240x800")
        self.minsize(1040, 700)

        self.owner_repo_var = StringVar(value=DEFAULT_OWNER_REPO)
        self.branch_var = StringVar(value=DEFAULT_BRANCH)
        self.repo_var = StringVar(value=str(cache_dir_for_repo(DEFAULT_OWNER_REPO)))
        self.target_var = StringVar(value="")
        self.message_var = StringVar(value="update KsText packs")
        self.bump_var = BooleanVar(value=True)
        self.summary_var = StringVar(value="未扫描")
        self.github_status_var = StringVar(value="检测中")
        self.pack_file_var = StringVar(value="")
        self.pack_id_var = StringVar(value="")
        self.pack_name_var = StringVar(value="")
        self.pack_author_var = StringVar(value="")
        self.pack_summary_var = StringVar(value="")
        self.pack_language_var = StringVar(value="zh-CN")
        self.pack_entries_var = StringVar(value="")
        self.status_var = StringVar(value="空闲")
        self.progress_var = DoubleVar(value=0.0)

        self.pack_documents: list[PackDocument] = []
        self.pack_warnings: list[str] = []
        self.existing_by_id: dict[str, dict] = {}
        self.current_index: int | None = None
        self.selection_guard = False
        self.suspend_dirty_tracking = False
        self.busy = False
        self.worker_queue: Queue[tuple[str, object]] = Queue()
        self.action_buttons: list[ttk.Button] = []
        self.base_summary_text = "未扫描"
        self.form_dirty = False
        self.index_dirty = False
        self.pending_sync_reload = False

        self._build_widgets()
        self._bind_dirty_tracking()
        self.github_status_var.set(infer_github_login_status())
        self.refresh_target_display()
        self.start_sync(sync_and_scan=True)

    def _build_widgets(self) -> None:
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill=BOTH, expand=True)

        form = ttk.Frame(frame)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="缓存仓库").grid(row=0, column=0, sticky=W, pady=4)
        ttk.Label(form, textvariable=self.repo_var).grid(row=0, column=1, columnspan=2, sticky=W, padx=8, pady=4)
        ttk.Button(form, text="同步仓库", command=self.sync_repository).grid(row=0, column=3, sticky="ew", pady=4)

        ttk.Label(form, text="owner/repo").grid(row=1, column=0, sticky=W, pady=4)
        ttk.Entry(form, textvariable=self.owner_repo_var).grid(row=1, column=1, sticky="ew", padx=8, pady=4)

        ttk.Label(form, text="分支").grid(row=1, column=2, sticky=W, padx=(8, 0), pady=4)
        ttk.Entry(form, textvariable=self.branch_var, width=18).grid(row=1, column=3, sticky="ew", pady=4)

        ttk.Label(form, text="提交信息").grid(row=2, column=0, sticky=W, pady=4)
        ttk.Entry(form, textvariable=self.message_var).grid(row=2, column=1, columnspan=3, sticky="ew", padx=8, pady=4)

        ttk.Label(form, text="GitHub").grid(row=3, column=0, sticky=W, pady=4)
        ttk.Label(form, textvariable=self.github_status_var).grid(row=3, column=1, columnspan=2, sticky=W, padx=8, pady=4)
        ttk.Button(form, text="刷新登录", command=self.refresh_github_status).grid(row=3, column=3, sticky="ew", pady=4)

        ttk.Label(form, text="当前目标").grid(row=4, column=0, sticky=W, pady=4)
        ttk.Label(form, textvariable=self.target_var).grid(row=4, column=1, columnspan=3, sticky=W, padx=8, pady=4)

        ttk.Checkbutton(form, text="变更包自动升级 version", variable=self.bump_var).grid(
            row=5, column=0, columnspan=4, sticky=W, pady=(4, 8)
        )

        action_bar = ttk.Frame(frame)
        action_bar.pack(fill="x", pady=(4, 10))
        self.action_buttons = [
            ttk.Button(action_bar, text="扫描仓库", command=self.scan_repository),
            ttk.Button(action_bar, text="缓存目录", command=self.show_cache_dir),
            ttk.Button(action_bar, text="清理当前缓存", command=self.cleanup_current_cache),
            ttk.Button(action_bar, text="新建包", command=self.create_pack),
            ttk.Button(action_bar, text="删除包", command=self.delete_pack),
            ttk.Button(action_bar, text="保存当前包", command=self.save_current_metadata),
            ttk.Button(action_bar, text="保存全部包", command=self.save_all_metadata),
            ttk.Button(action_bar, text="仅扫描预览", command=self.preview),
            ttk.Button(action_bar, text="重建 index.json", command=self.rebuild_index),
            ttk.Button(action_bar, text="提交并推送", command=self.commit_push),
            ttk.Button(action_bar, text="一键发布", command=self.full_publish),
        ]
        for index, button in enumerate(self.action_buttons):
            button.pack(side=LEFT, padx=(0 if index == 0 else 8, 0))

        ttk.Label(frame, textvariable=self.summary_var).pack(anchor=W, pady=(0, 8))

        status_frame = ttk.Frame(frame)
        status_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(status_frame, text="状态").pack(side=LEFT)
        ttk.Label(status_frame, textvariable=self.status_var).pack(side=LEFT, padx=(8, 12))
        self.progress_bar = ttk.Progressbar(status_frame, maximum=100, variable=self.progress_var)
        self.progress_bar.pack(side=LEFT, fill="x", expand=True)

        body = PanedWindow(frame, orient="horizontal")
        body.pack(fill=BOTH, expand=True)
        left = ttk.Frame(body, padding=(0, 0, 8, 0))
        right = ttk.Frame(body)
        body.add(left, width=440)
        body.add(right)

        self.pack_tree = ttk.Treeview(left, columns=("name", "author", "entries"), show="headings", height=18)
        self.pack_tree.heading("name", text="名字")
        self.pack_tree.heading("author", text="作者")
        self.pack_tree.heading("entries", text="条数")
        self.pack_tree.column("name", width=220, anchor=W)
        self.pack_tree.column("author", width=140, anchor=W)
        self.pack_tree.column("entries", width=60, anchor=W)
        self.pack_tree.pack(side=LEFT, fill=BOTH, expand=True)
        self.pack_tree.bind("<<TreeviewSelect>>", self.on_tree_select)

        tree_scroll = ttk.Scrollbar(left, orient=VERTICAL, command=self.pack_tree.yview)
        tree_scroll.pack(side=RIGHT, fill="y")
        self.pack_tree.configure(yscrollcommand=tree_scroll.set)

        editor = ttk.LabelFrame(right, text="当前包", padding=12)
        editor.pack(fill="x")
        editor.columnconfigure(1, weight=1)
        ttk.Label(editor, text="文件").grid(row=0, column=0, sticky=W, pady=4)
        ttk.Label(editor, textvariable=self.pack_file_var).grid(row=0, column=1, sticky=W, pady=4)
        ttk.Label(editor, text="ID").grid(row=1, column=0, sticky=W, pady=4)
        ttk.Entry(editor, textvariable=self.pack_id_var).grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Label(editor, text="名字").grid(row=2, column=0, sticky=W, pady=4)
        ttk.Entry(editor, textvariable=self.pack_name_var).grid(row=2, column=1, sticky="ew", pady=4)
        ttk.Label(editor, text="作者").grid(row=3, column=0, sticky=W, pady=4)
        ttk.Entry(editor, textvariable=self.pack_author_var).grid(row=3, column=1, sticky="ew", pady=4)
        ttk.Label(editor, text="简介").grid(row=4, column=0, sticky=W, pady=4)
        ttk.Entry(editor, textvariable=self.pack_summary_var).grid(row=4, column=1, sticky="ew", pady=4)
        ttk.Label(editor, text="语言").grid(row=5, column=0, sticky=W, pady=4)
        ttk.Entry(editor, textvariable=self.pack_language_var).grid(row=5, column=1, sticky="ew", pady=4)
        ttk.Label(editor, text="条数").grid(row=6, column=0, sticky=W, pady=4)
        ttk.Label(editor, textvariable=self.pack_entries_var).grid(row=6, column=1, sticky=W, pady=4)

        entries_frame = ttk.LabelFrame(right, text="文本内容", padding=12)
        entries_frame.pack(fill=BOTH, expand=True, pady=(10, 0))
        entries_frame.columnconfigure(0, weight=1)
        entries_frame.rowconfigure(0, weight=1)
        self.entries_box = ScrolledText(entries_frame, wrap=NONE, font=("Consolas", 10), height=18)
        self.entries_box.grid(row=0, column=0, sticky="nsew")
        entries_x_scroll = ttk.Scrollbar(entries_frame, orient=HORIZONTAL, command=self.entries_box.xview)
        entries_x_scroll.grid(row=1, column=0, sticky="ew")
        self.entries_box.configure(xscrollcommand=entries_x_scroll.set)

        ttk.Label(right, text="日志").pack(anchor=W, pady=(10, 4))
        self.log_box = ScrolledText(right, wrap="word", font=("Consolas", 10), height=10)
        self.log_box.pack(fill=BOTH, expand=True)

    def log(self, text: str) -> None:
        self.log_box.insert(END, text + "\n")
        self.log_box.see(END)
        self.update_idletasks()

    def _bind_dirty_tracking(self) -> None:
        for variable in [self.pack_id_var, self.pack_name_var, self.pack_author_var, self.pack_summary_var, self.pack_language_var]:
            variable.trace_add("write", self._on_form_field_changed)
        self.entries_box.bind("<<Modified>>", self._on_entries_modified)

    def _on_form_field_changed(self, *_args) -> None:
        self.refresh_dirty_state()

    def _on_entries_modified(self, _event=None) -> None:
        if self.entries_box.edit_modified():
            self.entries_box.edit_modified(False)
            self.refresh_dirty_state()

    def clear_log(self) -> None:
        self.log_box.delete("1.0", END)

    def set_base_summary(self, text: str) -> None:
        self.base_summary_text = text
        self.update_summary_label()

    def update_summary_label(self) -> None:
        markers: list[str] = []
        if self.form_dirty:
            markers.append("未保存")
        if self.index_dirty:
            markers.append("未重建")
        prefix = f"[{' / '.join(markers)}] " if markers else ""
        self.summary_var.set(prefix + self.base_summary_text)

    def current_form_snapshot(self) -> tuple[str, str, str, str, str, tuple[str, ...]] | None:
        if self.current_index is None or not (0 <= self.current_index < len(self.pack_documents)):
            return None
        entries = tuple(line.strip() for line in self.entries_box.get("1.0", END).splitlines() if line.strip())
        return (
            self.pack_id_var.get().strip(),
            self.pack_name_var.get().strip(),
            self.pack_author_var.get().strip(),
            self.pack_summary_var.get().strip(),
            self.pack_language_var.get().strip() or "zh-CN",
            entries,
        )

    def document_form_snapshot(self, doc: PackDocument) -> tuple[str, str, str, str, str, tuple[str, ...]]:
        return (doc.pack_id, doc.name, doc.author, doc.summary, doc.language or "zh-CN", tuple(doc.entries))

    def refresh_dirty_state(self) -> None:
        if self.suspend_dirty_tracking:
            return
        if self.current_index is None or not (0 <= self.current_index < len(self.pack_documents)):
            self.form_dirty = False
            self.update_summary_label()
            return
        self.form_dirty = self.current_form_snapshot() != self.document_form_snapshot(self.pack_documents[self.current_index])
        self.update_summary_label()

    def mark_saved(self) -> None:
        self.form_dirty = False
        self.index_dirty = True
        self.update_summary_label()

    def mark_rebuilt(self) -> None:
        self.form_dirty = False
        self.index_dirty = False
        self.update_summary_label()

    def reset_dirty_state(self) -> None:
        self.form_dirty = False
        self.index_dirty = False
        self.update_summary_label()

    def confirm_unsaved_changes(self, action_text: str) -> bool:
        if not self.form_dirty:
            return True
        choice = messagebox.askyesnocancel("KsText Publisher", f"当前包有未保存修改。{action_text} 前要先保存吗？", parent=self)
        if choice is None:
            return False
        if choice:
            self.save_current_metadata(show_message=False)
        return True

    def set_busy(self, busy: bool, status_text: str | None = None) -> None:
        self.busy = busy
        for button in self.action_buttons:
            button.configure(state=("disabled" if busy else "normal"))
        if status_text is not None:
            self.status_var.set(status_text)
        if not busy:
            self.progress_var.set(0.0)

    def set_progress_status(self, status_text: str, percent: float | None = None) -> None:
        self.status_var.set(status_text)
        if percent is not None:
            self.progress_var.set(max(0.0, min(100.0, percent)))

    def current_cache_repo_dir(self) -> Path:
        repo_dir = cache_dir_for_repo(self.owner_repo_var.get() or DEFAULT_OWNER_REPO)
        self.repo_var.set(str(repo_dir))
        return repo_dir

    def refresh_target_display(self) -> None:
        self.target_var.set(target_display(self.owner_repo_var.get(), self.branch_var.get(), self.current_cache_repo_dir()))

    def show_cache_dir(self) -> None:
        messagebox.showinfo("KsText Publisher", f"当前仓库缓存目录:\n{self.current_cache_repo_dir()}", parent=self)

    def cleanup_current_cache(self) -> None:
        if self.busy:
            return
        repo_dir = self.current_cache_repo_dir()
        if not repo_dir.exists():
            messagebox.showinfo("KsText Publisher", f"缓存目录不存在:\n{repo_dir}", parent=self)
            return
        if not messagebox.askyesno("KsText Publisher", f"确定清理当前缓存仓库吗？\n{repo_dir}", parent=self):
            return
        try:
            shutil.rmtree(repo_dir)
            self.pack_documents = []
            self.pack_warnings = []
            self.existing_by_id = {}
            self.current_index = None
            self.rebuild_tree()
            self.clear_log()
            self.log(f"已清理缓存仓库: {repo_dir}")
            self.set_base_summary("当前缓存已清理")
            self.reset_dirty_state()
            self.set_progress_status("空闲", 0)
        except Exception as exc:
            messagebox.showerror("KsText Publisher", str(exc))

    def refresh_github_status(self) -> None:
        self.github_status_var.set(infer_github_login_status())

    def start_sync(self, sync_and_scan: bool) -> None:
        if self.busy:
            return
        self.pending_sync_reload = sync_and_scan
        self.refresh_target_display()
        self.set_busy(True, "准备同步仓库")
        repo_dir = self.current_cache_repo_dir()
        owner_repo = normalize_owner_repo(self.owner_repo_var.get() or DEFAULT_OWNER_REPO)
        branch = clean_string(self.branch_var.get()) or DEFAULT_BRANCH
        worker = threading.Thread(target=self._sync_worker, args=(repo_dir, owner_repo, branch), daemon=True)
        worker.start()
        self.after(100, self.poll_worker_queue)

    def _sync_worker(self, repo_dir: Path, owner_repo: str, branch: str) -> None:
        try:
            message = sync_cached_repo(
                repo_dir,
                owner_repo,
                branch,
                progress_callback=lambda percent, text: self.worker_queue.put(("status", (text, percent))),
                output_callback=lambda text: self.worker_queue.put(("log", text)),
            )
            self.worker_queue.put(("sync_done", (repo_dir, owner_repo, branch, message)))
        except Exception as exc:
            self.worker_queue.put(("error", str(exc)))

    def sync_repository(self) -> None:
        self.start_sync(sync_and_scan=True)

    def resolve_inputs(self) -> tuple[Path, str, str]:
        repo_dir = ensure_repo_dir(str(self.current_cache_repo_dir()))
        owner_repo = normalize_owner_repo(self.owner_repo_var.get() or infer_owner_repo(repo_dir))
        branch = clean_string(self.branch_var.get()) or infer_branch(repo_dir)
        self.owner_repo_var.set(owner_repo)
        self.branch_var.set(branch)
        self.repo_var.set(str(cache_dir_for_repo(owner_repo)))
        self.refresh_target_display()
        return ensure_repo_dir(str(self.current_cache_repo_dir())), owner_repo, branch

    def persist_form_to_current(self) -> None:
        if self.current_index is None or not (0 <= self.current_index < len(self.pack_documents)):
            return
        doc = self.pack_documents[self.current_index]
        doc.pack_id = self.pack_id_var.get().strip()
        doc.name = self.pack_name_var.get().strip()
        doc.author = self.pack_author_var.get().strip()
        doc.summary = self.pack_summary_var.get().strip()
        doc.language = self.pack_language_var.get().strip() or "zh-CN"
        doc.entries = [line.strip() for line in self.entries_box.get("1.0", END).splitlines() if line.strip()]
        normalize_pack_document(doc)
        self.pack_entries_var.set(str(len(doc.entries)))
        self.refresh_tree_row(self.current_index)

    def load_current_form(self, index: int | None) -> None:
        self.suspend_dirty_tracking = True
        self.entries_box.delete("1.0", END)
        if index is None or not (0 <= index < len(self.pack_documents)):
            self.current_index = None
            self.pack_file_var.set("")
            self.pack_id_var.set("")
            self.pack_name_var.set("")
            self.pack_author_var.set("")
            self.pack_summary_var.set("")
            self.pack_language_var.set("zh-CN")
            self.pack_entries_var.set("")
            self.entries_box.edit_modified(False)
            self.suspend_dirty_tracking = False
            self.refresh_dirty_state()
            return
        doc = self.pack_documents[index]
        self.current_index = index
        self.pack_file_var.set(doc.path.name)
        self.pack_id_var.set(doc.pack_id)
        self.pack_name_var.set(doc.name)
        self.pack_author_var.set(doc.author)
        self.pack_summary_var.set(doc.summary)
        self.pack_language_var.set(doc.language)
        self.pack_entries_var.set(str(len(doc.entries)))
        self.entries_box.insert("1.0", "\n".join(doc.entries))
        self.entries_box.edit_modified(False)
        self.suspend_dirty_tracking = False
        self.refresh_dirty_state()

    def on_tree_select(self, _event=None) -> None:
        if self.selection_guard or self.busy:
            return
        items = self.pack_tree.selection()
        if not items:
            return
        target_index = int(items[0])
        if not self.confirm_unsaved_changes("切换包"):
            self.selection_guard = True
            if self.current_index is not None and self.pack_tree.exists(str(self.current_index)):
                self.pack_tree.selection_set(str(self.current_index))
                self.pack_tree.focus(str(self.current_index))
            self.selection_guard = False
            return
        try:
            self.persist_form_to_current()
        except PublishError as exc:
            messagebox.showerror("KsText Publisher", str(exc))
            return
        self.load_current_form(target_index)

    def rebuild_tree(self) -> None:
        self.selection_guard = True
        for item in self.pack_tree.get_children():
            self.pack_tree.delete(item)
        for index, doc in enumerate(self.pack_documents):
            self.pack_tree.insert("", END, iid=str(index), values=(doc.name, doc.author, len(doc.entries)))
        self.selection_guard = False
        if self.pack_documents:
            select_index = self.current_index if self.current_index is not None and self.current_index < len(self.pack_documents) else 0
            self.pack_tree.selection_set(str(select_index))
            self.pack_tree.focus(str(select_index))
            self.load_current_form(select_index)
        else:
            self.load_current_form(None)

    def refresh_tree_row(self, index: int) -> None:
        if self.pack_tree.exists(str(index)):
            doc = self.pack_documents[index]
            self.pack_tree.item(str(index), values=(doc.name, doc.author, len(doc.entries)))

    def sort_documents(self) -> None:
        self.pack_documents.sort(key=lambda item: item.path.name.lower())

    def reload_documents(self, repo_dir: Path) -> None:
        self.pack_documents, self.existing_by_id, self.pack_warnings = load_pack_documents(repo_dir)
        self.sort_documents()
        self.rebuild_tree()
        self.reset_dirty_state()

    def prepare_publish(self, repo_dir: Path, owner_repo: str, branch: str) -> BuildResult:
        self.persist_form_to_current()
        save_all_pack_documents(self.pack_documents)
        documents, existing_by_id, warnings, result = prepare_publish_assets(repo_dir, owner_repo, branch, self.bump_var.get())
        self.pack_documents = documents
        self.existing_by_id = existing_by_id
        self.pack_warnings = warnings
        self.sort_documents()
        self.rebuild_tree()
        write_index_file(repo_dir, result)
        self.mark_rebuilt()
        return result

    def scan_repository(self) -> None:
        if self.busy:
            return
        self.clear_log()
        try:
            repo_dir, _, _ = self.resolve_inputs()
            self.reload_documents(repo_dir)
            self.set_base_summary(f"已扫描 {len(self.pack_documents)} 个包")
            self.log(f"扫描完成: {repo_dir}")
            self.log(f"共 {len(self.pack_documents)} 个包")
            for warning in self.pack_warnings:
                self.log(f"[warn] {warning}")
            self.set_progress_status("空闲", 0)
        except Exception as exc:
            messagebox.showerror("KsText Publisher", str(exc))

    def create_pack(self) -> None:
        if self.busy:
            return
        try:
            repo_dir, _, _ = self.resolve_inputs()
            file_stem = simpledialog.askstring("新建包", "输入文件名（不用带 .json）", parent=self)
            if file_stem is None:
                return
            display_name = simpledialog.askstring("新建包", "输入显示名", initialvalue=file_stem, parent=self)
            if display_name is None:
                return
            doc = new_pack_document(repo_dir, file_stem, clean_string(display_name) or file_stem)
            save_pack_document(doc)
            self.pack_documents.append(doc)
            self.sort_documents()
            self.set_base_summary(f"已新建 {doc.path.name}")
            self.log(f"已新建 {doc.path.name}")
            self.rebuild_tree()
            self.mark_saved()
        except Exception as exc:
            messagebox.showerror("KsText Publisher", str(exc))

    def delete_pack(self) -> None:
        if self.busy:
            return
        try:
            self.persist_form_to_current()
            if self.current_index is None:
                raise PublishError("没有选中的包")
            doc = self.pack_documents[self.current_index]
            if not messagebox.askyesno("删除包", f"确定删除 {doc.path.name} ?\n这个操作会直接删文件。", parent=self):
                return
            if doc.path.exists():
                doc.path.unlink()
            self.pack_documents.pop(self.current_index)
            self.current_index = None
            self.rebuild_tree()
            self.log(f"已删除 {doc.path.name}")
            self.set_base_summary("已删除 1 个包")
            self.mark_saved()
        except Exception as exc:
            messagebox.showerror("KsText Publisher", str(exc))

    def save_current_metadata(self, show_message: bool = True) -> None:
        if self.busy:
            return
        try:
            self.persist_form_to_current()
            if self.current_index is None:
                raise PublishError("没有选中的包")
            save_pack_document(self.pack_documents[self.current_index])
            self.log(f"已保存 {self.pack_documents[self.current_index].path.name}")
            self.mark_saved()
            if show_message:
                messagebox.showinfo("KsText Publisher", "当前包已保存")
        except Exception as exc:
            messagebox.showerror("KsText Publisher", str(exc))

    def save_all_metadata(self, show_message: bool = True) -> None:
        if self.busy:
            return
        try:
            self.persist_form_to_current()
            save_all_pack_documents(self.pack_documents)
            self.log("已保存全部 packs/*.json")
            self.mark_saved()
            if show_message:
                messagebox.showinfo("KsText Publisher", "全部包已保存")
        except Exception as exc:
            messagebox.showerror("KsText Publisher", str(exc))

    def preview(self) -> None:
        if self.busy:
            return
        self.clear_log()
        try:
            _, owner_repo, branch = self.resolve_inputs()
            self.persist_form_to_current()
            result = build_index_from_documents(owner_repo, branch, self.pack_documents, self.existing_by_id, self.pack_warnings, self.bump_var.get())
            self.set_base_summary(build_summary(result))
            self.log(build_summary(result))
            preview_count = min(10, len(result.index_data["packs"]))
            for pack in result.index_data["packs"][:preview_count]:
                self.log(f"- {pack['name']} | id={pack['id']} | author={pack['author']} | entries={pack['entryCount']} | version={pack['version']}")
            if len(result.index_data["packs"]) > preview_count:
                self.log(f"... 其余 {len(result.index_data['packs']) - preview_count} 个包未展开")
        except Exception as exc:
            messagebox.showerror("KsText Publisher", str(exc))

    def rebuild_index(self) -> None:
        if self.busy:
            return
        self.clear_log()
        try:
            repo_dir, owner_repo, branch = self.resolve_inputs()
            result = self.prepare_publish(repo_dir, owner_repo, branch)
            self.set_base_summary(build_summary(result))
            self.log(build_summary(result))
            self.log(f"已写入 {repo_dir / 'index.json'}")
            self.set_progress_status("index.json 已重建", 100)
            messagebox.showinfo("KsText Publisher", "index.json 已重建")
            self.set_progress_status("空闲", 0)
        except Exception as exc:
            messagebox.showerror("KsText Publisher", str(exc))

    def start_push_worker(self, repo_dir: Path, message: str, success_summary: str, success_message: str) -> None:
        if self.busy:
            return
        self.set_busy(True, "准备提交")
        worker = threading.Thread(target=self._push_worker, args=(repo_dir, message, success_summary, success_message), daemon=True)
        worker.start()
        self.after(100, self.poll_worker_queue)

    def _push_worker(self, repo_dir: Path, message: str, success_summary: str, success_message: str) -> None:
        try:
            self.worker_queue.put(("status", ("正在暂存文件", 0.0)))
            run_git(repo_dir, "add", "index.json", "packs")
            if not git_status(repo_dir):
                self.worker_queue.put(("done", ("没有可提交的改动", "没有可提交的改动", False)))
                return
            self.worker_queue.put(("status", ("正在提交", 5.0)))
            commit_output = run_git(repo_dir, "commit", "-m", message).strip()
            if commit_output:
                self.worker_queue.put(("log", commit_output))
            branch = infer_branch(repo_dir)
            self.worker_queue.put(("status", (f"开始推送到 origin/{branch}", 10.0)))
            push_output = run_git_push_with_progress(
                repo_dir,
                branch,
                progress_callback=lambda percent, text: self.worker_queue.put(("status", (text, percent))),
                output_callback=lambda text: self.worker_queue.put(("log", text)),
            )
            if push_output:
                self.worker_queue.put(("log", push_output))
            self.worker_queue.put(("done", (success_summary, success_message, True)))
        except Exception as exc:
            self.worker_queue.put(("error", str(exc)))

    def poll_worker_queue(self) -> None:
        try:
            while True:
                kind, payload = self.worker_queue.get_nowait()
                if kind == "log":
                    self.log(str(payload))
                elif kind == "status":
                    status_text, percent = payload
                    self.set_progress_status(str(status_text), float(percent) if percent is not None else None)
                elif kind == "sync_done":
                    repo_dir, owner_repo, branch, message = payload
                    self.owner_repo_var.set(normalize_owner_repo(owner_repo))
                    self.branch_var.set(branch)
                    self.repo_var.set(str(repo_dir))
                    self.refresh_target_display()
                    self.github_status_var.set(infer_github_login_status())
                    self.log(str(message))
                    self.set_base_summary(f"已同步: {repo_dir}")
                    if self.pending_sync_reload:
                        self.reload_documents(repo_dir)
                        self.set_base_summary(f"已扫描 {len(self.pack_documents)} 个包")
                    self.pending_sync_reload = False
                    self.set_busy(False, "同步完成")
                    self.set_progress_status("空闲", 0)
                    return
                elif kind == "done":
                    summary_text, info_text, changed = payload
                    self.set_busy(False, "空闲")
                    self.set_base_summary(str(summary_text))
                    if changed:
                        self.set_progress_status("推送完成", 100)
                    messagebox.showinfo("KsText Publisher", str(info_text))
                    self.set_progress_status("空闲", 0)
                    return
                elif kind == "error":
                    self.pending_sync_reload = False
                    self.set_busy(False, "失败")
                    messagebox.showerror("KsText Publisher", str(payload))
                    self.set_progress_status("空闲", 0)
                    return
        except Empty:
            pass
        if self.busy:
            self.after(100, self.poll_worker_queue)

    def commit_push(self) -> None:
        if self.busy:
            return
        self.clear_log()
        try:
            repo_dir, owner_repo, branch = self.resolve_inputs()
            result = self.prepare_publish(repo_dir, owner_repo, branch)
            self.set_base_summary(build_summary(result))
            self.log(build_summary(result))
            self.log(f"已写入 {repo_dir / 'index.json'}")
            message = self.message_var.get().strip() or "update KsText packs"
            self.start_push_worker(repo_dir, message, "提交并推送完成", "提交并推送完成")
        except Exception as exc:
            messagebox.showerror("KsText Publisher", str(exc))

    def full_publish(self) -> None:
        if self.busy:
            return
        self.commit_push()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KsText 自动重建 index.json 并推送仓库")
    parser.add_argument("--repo", default="")
    parser.add_argument("--owner-repo", default="")
    parser.add_argument("--branch", default="")
    parser.add_argument("--message", default="update KsText packs")
    parser.add_argument("--write-index", action="store_true")
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-bump-version", action="store_true")
    parser.add_argument("--gui", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.gui or len(argv) == 0:
        app = PublisherApp()
        app.mainloop()
        return 0
    return run_cli(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
