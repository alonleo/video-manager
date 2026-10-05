import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { addTag, listTags, removeTag } from "../api/client";
import type { Tag, VideoDetail } from "../api/types";
import Icon from "./Icon";
import "./VideoTagPicker.css";

type TagChange =
  | { type: "add"; name: string; clearSearch?: boolean }
  | { type: "remove"; tag: Tag };

export default function VideoTagPicker({ video }: { video: VideoDetail }) {
  const qc = useQueryClient();
  const titleId = useId();
  const hintId = useId();
  const dropdownId = useId();
  const [open, setOpen] = useState(false);
  const [placement, setPlacement] = useState({ above: false, maxHeight: 360 });
  const dropdown = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const searchInput = useRef<HTMLInputElement>(null);

  useLayoutEffect(() => {
    if (!open) return;
    const position = () => {
      const rect = trigger.current?.getBoundingClientRect();
      if (!rect) return;
      const below = window.innerHeight - rect.bottom;
      const above = below < 360 && rect.top > below;
      setPlacement({ above, maxHeight: Math.max(120, Math.min(400, (above ? rect.top : below) - 16)) });
    };
    position();
    searchInput.current?.focus({ preventScroll: true });
    window.addEventListener("resize", position);
    window.addEventListener("scroll", position, true);
    return () => {
      window.removeEventListener("resize", position);
      window.removeEventListener("scroll", position, true);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const dismiss = (event: PointerEvent) => {
      if (event.target instanceof Node && !dropdown.current?.contains(event.target)) setOpen(false);
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [open]);
  const [search, setSearch] = useState("");
  const [notice, setNotice] = useState("");
  const saving = useRef(false);
  const tags = useQuery({ queryKey: ["tags", video.media_type], queryFn: () => listTags(video.media_type) });
  const selectedIds = new Set(video.tags.map((tag) => tag.id));
  const query = search.trim();
  const normalizedQuery = query.toLocaleLowerCase();
  const visible = (tags.data ?? []).filter((tag) =>
    tag.name.toLocaleLowerCase().includes(normalizedQuery),
  );
  const exact = [...(tags.data ?? []), ...video.tags].find(
    (tag) => tag.name.toLocaleLowerCase() === normalizedQuery,
  );

  const mutation = useMutation({
    mutationFn: (change: TagChange) =>
      change.type === "remove"
        ? removeTag(video.id, change.tag.id)
        : addTag(video.id, change.name),
    onMutate: () => qc.cancelQueries({ queryKey: ["video", video.id] }),
    onSuccess: async (updatedTags, change) => {
      qc.setQueryData<VideoDetail>(["video", video.id], (old) =>
        old ? { ...old, tags: updatedTags } : old,
      );
      if (change.type === "add" && change.clearSearch) setSearch("");
      setNotice(
        change.type === "remove"
          ? `已从此影像移除「${change.tag.name}」`
          : `已添加「${change.name}」`,
      );
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["tags"] }),
        qc.invalidateQueries({ queryKey: ["video", video.id] }),
        qc.invalidateQueries({ queryKey: ["videos"] }),
      ]);
    },
    onSettled: () => { saving.current = false; },
  });

  function save(change: TagChange) {
    if (saving.current) return;
    saving.current = true;
    setNotice("");
    mutation.mutate(change);
  }

  function submit() {
    if (!query || !tags.isSuccess || tags.isError) return;
    if (exact) {
      if (!selectedIds.has(exact.id)) save({ type: "add", name: exact.name });
    } else {
      save({ type: "add", name: query, clearSearch: true });
    }
  }

  return (
    <section className="card video-tag-picker" aria-labelledby={titleId}>
      <header className="video-tag-heading">
        <h2 id={titleId}>标签</h2>
        <span>已添加 {video.tags.length}</span>
      </header>
      <div className="video-tag-selected" aria-label="此影像的标签">
        {!video.tags.length && <p className="video-tag-muted">尚未添加标签</p>}
        {video.tags.map((tag) => (
          <span className="video-tag-chip" key={tag.id}>
            <span>{tag.name}</span>
            <button
              type="button"
              className="tag-remove"
              aria-label={`移除标签 ${tag.name}`}
              title="从此影像移除"
              disabled={mutation.isPending}
              onClick={() => save({ type: "remove", tag })}
            >
              <Icon name="close" size={13} />
            </button>
          </span>
        ))}
      </div>
      <p id={hintId} className="video-tag-hint">
        下拉选择标签，支持搜索和自定义添加。
      </p>
      <div
        className="video-tag-dropdown"
        ref={dropdown}
        onBlur={(event) => {
          if (event.relatedTarget instanceof Node && !event.currentTarget.contains(event.relatedTarget)) setOpen(false);
        }}
        onKeyDown={(event) => {
          if (event.nativeEvent.isComposing) return;
          if (event.key === "Escape" && open) {
            event.preventDefault();
            event.stopPropagation();
            setOpen(false);
            trigger.current?.focus();
          }
        }}
      >
        <button
          ref={trigger}
          type="button"
          className="video-tag-trigger"
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-controls={open ? dropdownId : undefined}
          onClick={() => setOpen((value) => !value)}
          onKeyDown={(event) => {
            if (event.key === "ArrowDown") {
              event.preventDefault();
              setOpen(true);
              searchInput.current?.focus();
            }
          }}
        >
          <span>选择或添加标签</span>
          <Icon name="chevron" size={16} className={open ? "is-open" : ""} />
        </button>
        {open && (
          <div
            id={dropdownId}
            role="dialog"
            aria-label="选择标签"
            className={placement.above ? "video-tag-menu opens-above" : "video-tag-menu"}
            style={{ maxHeight: placement.maxHeight }}
          >
      <form
        className="video-tag-form"
        onSubmit={(event) => { event.preventDefault(); submit(); }}
      >
        <label className="video-tag-search">
          <span className="sr-only">搜索或自定义标签</span>
          <Icon name="search" size={16} />
          <input
            ref={searchInput}
            type="search"
            placeholder="搜索或输入新标签…"
            value={search}
            aria-describedby={hintId}
            disabled={mutation.isPending}
            onChange={(event) => setSearch(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" &&
                  (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229)) {
                event.preventDefault();
              }
            }}
          />
        </label>
        {query && !exact && tags.isSuccess && !tags.isError && (
          <button
            type="submit"
            className="btn-outline video-tag-create"
            disabled={mutation.isPending}
          >
            <Icon name="plus" size={15} />
            <span>创建并添加「{query}」</span>
          </button>
        )}
      </form>
      {tags.isLoading && <p className="video-tag-empty" role="status">正在加载标签…</p>}
      {tags.isError && (
        <div className="inline-alert video-tag-error" role="alert">
          <p>标签列表加载失败：{tags.error.message}</p>
          <button
            type="button"
            className="btn-outline"
            disabled={tags.isFetching}
            onClick={() => void tags.refetch()}
          >
            重新加载标签
          </button>
        </div>
      )}
      {tags.data && (
        <>
          <p className="video-tag-count" aria-live="polite">
            {query ? `找到 ${visible.length} 个 / 共 ${tags.data.length} 个` : `全部标签 · ${tags.data.length}`}
          </p>
          <ul className="video-tag-options" aria-label="现有标签列表">
            {visible.map((tag) => {
              const selected = selectedIds.has(tag.id);
              return (
                <li key={tag.id}>
                  <button
                    type="button"
                    className={selected ? "video-tag-option is-selected" : "video-tag-option"}
                    aria-label={`标签 ${tag.name}`}
                    aria-pressed={selected}
                    disabled={mutation.isPending}
                    onClick={() => save(
                      selected ? { type: "remove", tag } : { type: "add", name: tag.name },
                    )}
                  >
                    <span className="video-tag-option-copy">
                      <strong>{tag.name}</strong>
                      <small>{tag.video_count} 个影像</small>
                    </span>
                    <span className="video-tag-option-state">
                      <Icon name={selected ? "check" : "plus"} size={14} />
                      {selected ? "已添加" : "添加"}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
          {!visible.length && (
            <p className="video-tag-empty">
              {query ? "没有匹配的标签，可创建新标签。" : "还没有标签，输入名称即可创建。"}
            </p>
          )}
        </>
      )}
          </div>
        )}
      </div>
      <p className="video-tag-status" role="status">
        {mutation.isPending ? "正在保存…" : notice}
      </p>
      {mutation.isError && (
        <div className="inline-alert video-tag-error" role="alert">
          保存失败：{mutation.error.message}。请重试。
        </div>
      )}
    </section>
  );
}
