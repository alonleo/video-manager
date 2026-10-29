import { useEffect, useRef, useState, type ComponentRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Input, Modal } from "@arco-design/web-react";
import { ApiError, deleteTag, listTags } from "../api/client";
import type { TagSummary } from "../api/types";
import Icon from "./Icon";
import "./TagManager.css";

export default function TagManager({
  mediaType,
  onDeleted,
}: {
  mediaType: "video" | "photo";
  onDeleted: (tag: TagSummary) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<TagSummary | null>(null);
  const [notice, setNotice] = useState("");
  const trigger = useRef<HTMLButtonElement>(null);
  const searchInput = useRef<ComponentRef<typeof Input>>(null);
  const cancelButton = useRef<HTMLButtonElement>(null);
  const qc = useQueryClient();
  const tags = useQuery({
    queryKey: ["tags", mediaType],
    queryFn: () => listTags(mediaType),
    enabled: open,
  });

  const deletion = useMutation({
    mutationFn: (tag: TagSummary) => deleteTag(tag.id, tag.video_count, mediaType),
    retry: false,
    onSuccess: (_data, tag) => {
      qc.setQueryData<TagSummary[]>(["tags", mediaType], (old) =>
        old?.filter((t) => t.id !== tag.id),
      );
      void qc.invalidateQueries({ queryKey: ["tags"] });
      void qc.invalidateQueries({ queryKey: ["videos"] });
      void qc.invalidateQueries({ queryKey: ["video"] });
      setSelected(null);
      setNotice(`已删除标签「${tag.name}」，原文件保持不变。`);
      onDeleted(tag);
    },
    onError: (error) => {
      if (
        error instanceof ApiError &&
        (error.status === 409 || error.status === 404)
      ) {
        setSelected(null);
        void qc.invalidateQueries({ queryKey: ["tags"] });
      }
    },
  });

  useEffect(() => {
    if (!open) return;
    if (selected) cancelButton.current?.focus();
    else searchInput.current?.focus();
  }, [selected, open]);

  const visible = (tags.data ?? []).filter((t) =>
    t.name.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()),
  );

  return (
    <>
      <Button
        ref={trigger}
        className="tag-manager-trigger"
        onClick={() => {
          setSearch("");
          setSelected(null);
          setNotice("");
          deletion.reset();
          void tags.refetch();
          setOpen(true);
        }}
      >
        <Icon name="list" size={16} />
        管理标签
      </Button>
      <Modal
        visible={open}
        className="tag-manager-dialog"
        escToExit={!deletion.isPending}
        footer={null}
        title={
          <div>
            <p className="eyebrow">
              <span /> ORGANIZE YOUR ARCHIVE
            </p>
            <h2>{selected ? "删除标签" : "管理标签"}</h2>
          </div>
        }
        onCancel={() => {
          if (deletion.isPending) return;
          setOpen(false);
          trigger.current?.focus();
        }}
      >
        <div className="tag-manager-body">
          <p className="tag-manager-description">
            {selected
              ? "删除当前库的标签及其关联，另一个媒体库的标签不受影响。"
              : "整理当前媒体库的标签，视频和照片的标签分别管理。"}
          </p>
          {notice && (
            <p className="tag-manager-notice" role="status">
              {notice}
            </p>
          )}
          {deletion.isError && (
            <p className="inline-alert" role="alert">
              {deletion.error.message}
            </p>
          )}
          {selected ? (
            <>
              <div className="tag-delete-preview">
                <strong>{selected.name}</strong>
                <span>{selected.video_count} 个影像正在使用</span>
              </div>
              <p className="tag-manager-description">
                {selected.video_count
                  ? `确认后，将从这 ${selected.video_count} 个影像中移除「${selected.name}」，并从标签库删除它。`
                  : "此标签没有关联影像，确认后将从标签库删除。"}
                原文件、其他标签和观看进度都会保留。
              </p>
              <div className="tag-manager-actions">
                <Button
                  ref={cancelButton}
                  disabled={deletion.isPending}
                  onClick={() => {
                    setSelected(null);
                    deletion.reset();
                  }}
                >
                  取消
                </Button>
                <Button
                  type="primary"
                  loading={deletion.isPending}
                  onClick={() => deletion.mutate(selected)}
                >
                  {deletion.isPending ? "正在删除…" : "确认删除标签"}
                </Button>
              </div>
            </>
          ) : (
            <>
              <label className="tag-manager-search">
                <span className="sr-only">搜索标签</span>
                <Icon name="search" size={17} />
                <Input
                  ref={searchInput}
                  placeholder="搜索标签名称…"
                  value={search}
                  onChange={setSearch}
                />
              </label>
              {tags.isLoading && (
                <p role="status" className="tag-manager-empty">
                  正在读取标签…
                </p>
              )}
              {tags.isError && (
                <div role="alert" className="inline-alert">
                  无法读取标签：{tags.error.message}
                  <Button onClick={() => tags.refetch()}>
                    重新加载标签
                  </Button>
                </div>
              )}
              {!tags.isLoading && !tags.isError && (
                <>
                  <p className="tag-manager-count">
                    {search
                      ? `找到 ${visible.length} 个标签`
                      : `共 ${visible.length} 个标签`}
                  </p>
                  <ul className="tag-manager-list">
                    {visible.map((tag) => (
                      <li key={tag.id}>
                        <div>
                          <strong title={tag.name}>{tag.name}</strong>
                          <span>{tag.video_count} 个影像</span>
                        </div>
                        <Button
                          className="tag-delete-button"
                          aria-label={`删除标签「${tag.name}」`}
                          disabled={tags.isFetching}
                          onClick={() => {
                            deletion.reset();
                            setNotice("");
                            setSelected(tag);
                          }}
                        >
                          删除
                        </Button>
                      </li>
                    ))}
                  </ul>
                  {!visible.length && (
                    <p className="tag-manager-empty">
                      {search
                        ? "没有找到匹配的标签。"
                        : "标签库为空。可在视频播放页或照片预览页添加标签。"}
                    </p>
                  )}
                </>
              )}
            </>
          )}
        </div>
      </Modal>
    </>
  );
}
