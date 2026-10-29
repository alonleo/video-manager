import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import Icon from "../components/Icon";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Input, Modal, Select } from "@arco-design/web-react";

import {
  browseStorage,
  createStorage,
  deleteStorage,
  listStorages,
  scanStorage,
  testStorage,
} from "../api/client";
import type { Storage } from "../api/types";
import { cn } from "../lib/cn";
import { formatRelative } from "../lib/format";

const TYPE_LABEL: Record<string, string> = {
  local: "本地目录 / NAS",
  openlist: "网盘（OpenList）",
  url: "在线链接",
};

const TYPE_OPTIONS = Object.entries(TYPE_LABEL).map(([k, label]) => ({
  label: k !== "local" ? `${label} · 即将支持` : label,
  value: k,
  disabled: k !== "local",
}));

function AddStorageForm({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState("");
  const nameRef = useRef<{ blur: () => void; focus: () => void; dom: HTMLInputElement } | null>(null);
  useEffect(() => {
    nameRef.current?.focus();
  }, []);
  const [type, setType] = useState<"local" | "openlist" | "url">("local");
  const [root, setRoot] = useState("");
  const [baseUrl, setBaseUrl] = useState("http://127.0.0.1:5244");
  const [mount, setMount] = useState("/");
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () => {
      const config: Record<string, unknown> =
        type === "local"
          ? { root: root.trim() }
          : type === "openlist"
            ? { base_url: baseUrl, mount }
            : { extractors: ["bilibili", "douyin", "youtube"] };
      return createStorage({ name: name.trim(), type, config });
    },
    onSuccess: () => onDone(),
    onError: (e: Error) => setError(e.message),
  });

  return (
    <form
      className="card storage-form flex flex-col gap-3 p-4"
      onSubmit={(e) => {
        e.preventDefault();
        setError(null);
        create.mutate();
      }}
    >
      <div className="storage-form-heading">
        <h2>连接新的存储源</h2>
        <Button
          size="mini"
          icon={<Icon name="close" size={17} />}
          aria-label="关闭添加表单"
          onClick={onDone}
        />
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1">
          <span className="text-xs text-muted">名称</span>
          <Input
            ref={nameRef}
            value={name}
            onChange={setName}
            placeholder="我的视频盘"
            required
          />
        </label>

        <label className="flex flex-col gap-1">
          <span className="text-xs text-muted">类型</span>
          <Select
            style={{ width: "100%" }}
            value={type}
            options={TYPE_OPTIONS}
            onChange={(value) => setType(value as typeof type)}
          />
        </label>
      </div>

      {type === "local" && (
        <label className="flex flex-col gap-1">
          <span className="text-xs text-muted">根目录（绝对路径）</span>
          <Input
            className="font-mono"
            value={root}
            onChange={setRoot}
            placeholder="/mnt/videos"
            required
          />
          <span className="text-[11px] text-muted">
            外接盘/NAS 的挂载点。原始文件留在原地，不会被拷贝。
          </span>
        </label>
      )}

      {type === "openlist" && (
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="flex flex-col gap-1">
            <span className="text-xs text-muted">OpenList 地址</span>
            <Input
              className="font-mono"
              value={baseUrl}
              onChange={setBaseUrl}
              required
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-muted">挂载路径</span>
            <Input
              className="font-mono"
              value={mount}
              onChange={setMount}
              placeholder="/aliyun"
            />
          </label>
        </div>
      )}

      {type === "url" && (
        <p className="text-xs text-muted">
          在线链接源支持 B 站 / 抖音 /
          YouTube。快手、小红书无可靠的自动抓取方案，
          请下载后手动放入本地目录再扫描。
        </p>
      )}

      {error && (
        <div className="inline-alert" role="alert">
          {error}
        </div>
      )}

      <div className="flex gap-2">
        <Button
          type="primary"
          htmlType="submit"
          loading={create.isPending}
          disabled={!name.trim() || (type === "local" && !root.trim())}
        >
          创建
        </Button>
        <Button onClick={onDone}>取消</Button>
      </div>
    </form>
  );
}

function StorageRow({ storage }: { storage: Storage }) {
  const qc = useQueryClient();
  const [scanPath, setScanPath] = useState("");
  const [expanded, setExpanded] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const test = useMutation({
    mutationFn: () => testStorage(storage.id),
    onSuccess: (r) => setMsg(r.ok ? `✓ ${r.message}` : `✗ ${r.message}`),
    onError: (e: Error) => setMsg(`✗ ${e.message}`),
  });

  const scan = useMutation({
    mutationFn: () => scanStorage(storage.id, scanPath, true),
    onSuccess: (r) => {
      setMsg(
        `✓ ${r.message}${r.job_id != null ? `（任务 #${r.job_id}）` : ""}`,
      );
      qc.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: (e: Error) => setMsg(`✗ ${e.message}`),
  });

  const browse = useQuery({
    queryKey: ["browse", storage.id, scanPath],
    queryFn: () => browseStorage(storage.id, scanPath),
    enabled: expanded && storage.type === "local",
  });

  const del = useMutation({
    mutationFn: () => deleteStorage(storage.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["storages"] });
      qc.invalidateQueries({ queryKey: ["videos"] });
    },
    onError: (e: Error) => setMsg(`✗ ${e.message}`),
  });

  const root =
    storage.type === "local"
      ? String((storage.config as { root?: string }).root ?? "")
      : String((storage.config as { base_url?: string }).base_url ?? "");

  return (
    <div className="card p-4">
      <div className="flex flex-wrap items-center gap-3">
        <span
          className={cn(
            "chip",
            storage.type === "local" && "bg-emerald-500/15 text-emerald-400",
            storage.type === "openlist" && "bg-sky-500/15 text-sky-400",
            storage.type === "url" && "bg-purple-500/15 text-purple-400",
          )}
        >
          {TYPE_LABEL[storage.type]}
        </span>

        <span className="font-medium">{storage.name}</span>
        <span
          className="min-w-0 flex-1 truncate font-mono text-xs text-muted"
          title={root}
        >
          {root}
        </span>
        <span className="text-xs text-muted">
          上次扫描：
          {storage.last_scan_at ? formatRelative(storage.last_scan_at) : "从未"}
        </span>

        <div className="flex gap-1.5">
          <Button
            loading={test.isPending}
            onClick={() => test.mutate()}
            disabled={test.isPending}
          >
            检测
          </Button>
          <Button
            loading={scan.isPending}
            onClick={() => scan.mutate()}
            disabled={scan.isPending}
          >
            扫描
          </Button>
          {storage.type === "local" && (
            <Button onClick={() => setExpanded((v) => !v)}>
              {expanded ? "收起" : "浏览"}
            </Button>
          )}
          <Button
            status="danger"
            disabled={del.isPending}
            onClick={() => {
              Modal.confirm({
                title: "删除存储源",
                content: (
                  <span>
                    确定删除存储源「{storage.name}」？
                    <br />
                    只会删除配置，不会删除任何原文件。
                  </span>
                ),
                okText: "删除",
                cancelText: "取消",
                onOk: () => del.mutate(),
              });
            }}
          >
            删除
          </Button>
        </div>
      </div>

      {msg && (
        <div
          role="status"
          className={cn(
            "mt-2 text-xs",
            msg.startsWith("✓") ? "text-emerald-400" : "text-red-400",
          )}
        >
          {msg}
        </div>
      )}

      {expanded && storage.type === "local" && (
        <div className="mt-3 rounded border border-border bg-panel2 p-3">
          <div className="mb-2 flex items-center gap-2 text-xs">
            <span className="font-mono text-muted">/{scanPath}</span>
            {scanPath && (
              <button
                className="text-accent hover:underline"
                onClick={() => {
                  const parts = scanPath.split("/").filter(Boolean);
                  parts.pop();
                  setScanPath(parts.join("/"));
                }}
              >
                上一级
              </button>
            )}
            {scanPath && (
              <button
                className="text-accent hover:underline"
                onClick={() => setScanPath("")}
              >
                回根目录
              </button>
            )}
          </div>

          {browse.isLoading && (
            <div className="text-xs text-muted">读取中…</div>
          )}
          {browse.isError && (
            <div className="text-xs text-red-400">
              {(browse.error as Error).message}
            </div>
          )}

          <div className="max-h-56 overflow-auto">
            {browse.data?.entries.map((e) =>
              e.is_dir ? (
                <button
                  key={e.path}
                  className="flex w-full items-center gap-2 rounded px-2 py-1 text-left text-xs hover:bg-panel"
                  onClick={() => setScanPath(e.path)}
                >
                  <Icon name="folder" size={15} />
                  <span className="truncate">{e.name}</span>
                </button>
              ) : (
                <div
                  key={e.path}
                  className="flex items-center gap-2 px-2 py-1 text-xs text-muted"
                >
                  <span>🎬</span>
                  <span className="truncate">{e.name}</span>
                </div>
              ),
            )}
            {browse.data?.entries.length === 0 && (
              <div className="px-2 py-1 text-xs text-muted">空目录</div>
            )}
          </div>

          <div className="mt-3 flex items-center gap-2">
            <Input
              className="font-mono text-xs"
              placeholder="子路径（留空扫描全部）"
              aria-label="要扫描的子目录路径"
              value={scanPath}
              onChange={setScanPath}
            />
            <Button
              type="primary"
              className="shrink-0"
              loading={scan.isPending}
              onClick={() => scan.mutate()}
            >
              扫描此路径
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

export default function StoragesPage() {
  const [params, setParams] = useSearchParams();
  const adding = params.get("add") === "1";
  const setAdding = (open: boolean) => setParams(open ? { add: "1" } : {});
  const qc = useQueryClient();
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["storages"],
    queryFn: listStorages,
  });

  return (
    <div className="page content-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">
            <span /> CONNECT YOUR COLLECTION
          </p>
          <h1>散落各处，汇聚于此。</h1>
          <p className="page-intro">
            连接你的存储空间，为每段影像建立一个入口。
          </p>
        </div>
        {!adding && (
          <Button
            type="primary"
            className="ml-auto"
            icon={<Icon name="plus" size={16} />}
            onClick={() => setAdding(true)}
          >
            添加存储源
          </Button>
        )}
      </div>

      <p className="mb-4 text-sm text-muted" style={{ lineHeight: 1.9 }}>
        原始文件保留在原始位置（外接盘 / NAS /
        网盘），本平台只索引元数据与封面， 不拷贝原文件。
      </p>

      <div className="source-guide">
        <div>
          <Icon name="storage" />
          <h2>本地目录与 NAS</h2>
          <p>连接硬盘或已挂载的 NAS 目录，自动识别影像与媒体信息。</p>
          <small>现已支持 · 按需读取</small>
        </div>
        <div>
          <Icon name="folder" />
          <h2>云端的收藏</h2>
          <p>通过统一网盘入口，汇集不同云端空间的珍藏。</p>
          <small>即将支持 · 网盘接入</small>
        </div>
        <div>
          <Icon name="film" />
          <h2>网上遇见的灵感</h2>
          <p>让喜欢的在线影像，也拥有一个专属的归档位置。</p>
          <small>即将支持 · 链接导入</small>
        </div>
      </div>
      {adding && (
        <div className="mb-4">
          <AddStorageForm
            onDone={() => {
              setAdding(false);
              qc.invalidateQueries({ queryKey: ["storages"] });
            }}
          />
        </div>
      )}

      {isLoading && <div className="py-12 text-center text-muted">加载中…</div>}
      {isError && (
        <div className="card p-6 text-center text-red-400">
          加载失败：{(error as Error).message}
        </div>
      )}

      <div className="source-list flex flex-col gap-3">
        {data?.map((s) => (
          <StorageRow key={s.id} storage={s} />
        ))}
      </div>

      {data?.length === 0 && !adding && (
        <div className="empty-state">
          <Icon name="storage" size={32} />
          <h3>你的第一处影像空间</h3>
          <p>从一个本地目录开始。完成连接后，点击扫描即可建立影像索引。</p>
          <Button type="primary" onClick={() => setAdding(true)}>
            添加第一个存储源
          </Button>
        </div>
      )}
    </div>
  );
}
