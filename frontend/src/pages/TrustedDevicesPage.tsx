import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { QRCodeSVG } from "qrcode.react";

import {
  createPairing,
  decidePairing,
  getPairing,
  getPairingOrigins,
  listPendingPairings,
  listTrustedDevices,
  revokeTrustedDevice,
  type Pairing,
} from "../api/deviceAuth";
import Icon from "../components/Icon";
import {
  buildPairingPayload,
  normalizePairingOrigin,
  type PairingMode,
} from "../lib/pairingPayload";
import "../components/trusted-devices.css";

function formatTime(value: string | null) {
  if (!value) return "尚未使用";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function PairingPanel({ pairing, close }: { pairing: Pairing; close: () => void }) {
  const qc = useQueryClient();
  const [mode, setMode] = useState<PairingMode>("android");
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  const [siteOrigin, setSiteOrigin] = useState(window.location.origin);
  const [originEdited, setOriginEdited] = useState(false);
  const pairingOrigins = useQuery({
    queryKey: ["device-pairing-origins"],
    queryFn: getPairingOrigins,
    staleTime: 60_000,
  });
  useEffect(() => {
    if (originEdited || !pairingOrigins.data?.recommended) return;
    try {
      normalizePairingOrigin(window.location.origin, "android");
    } catch {
      setSiteOrigin(pairingOrigins.data.recommended);
    }
  }, [originEdited, pairingOrigins.data?.recommended]);
  const pairTarget = useMemo(() => {
    try {
      const value = buildPairingPayload(mode, siteOrigin, pairing.token ?? "");
      return { url: value, error: "" };
    } catch (error) {
      return {
        url: "",
        error: error instanceof Error ? error.message : "无法生成配对链接，请检查服务器地址",
      };
    }
  }, [mode, pairing.token, siteOrigin]);
  const current = useQuery({
    queryKey: ["device-pairing", pairing.id],
    queryFn: () => getPairing(pairing.id),
    initialData: pairing,
    refetchInterval: query => ["open", "pending"].includes(query.state.data?.status ?? "") ? 1500 : false,
  });
  const decide = useMutation({
    mutationFn: (decision: "approve" | "reject") => decidePairing(pairing.id, decision),
    onSuccess: data => {
      qc.setQueryData(["device-pairing", pairing.id], data);
      void qc.invalidateQueries({ queryKey: ["trusted-devices"] });
      void qc.invalidateQueries({ queryKey: ["pending-device-pairings"] });
    },
  });
  const data = current.data;
  const pending = data.status === "pending" && data.requested_device;

  return <section className="card pairing-card" aria-live="polite">
    <div className="pairing-card-heading">
      <div><p className="eyebrow"><span /> ADD DEVICE · 添加设备</p><h2>扫描二维码</h2></div>
      <button className="btn-ghost" type="button" onClick={close} aria-label="关闭二维码"><Icon name="close" size={18} /></button>
    </div>
    {data.status === "open" && <>
      <p>在新设备上使用相机扫码，然后回到这里核对验证码并批准。</p>
      <fieldset className="pairing-mode">
        <legend>扫码设备</legend>
        <div className="pairing-mode-options">
          <label className="pairing-mode-option">
            <input type="radio" name="pairing-mode" checked={mode === "android"} onChange={event => {
              if (!event.currentTarget.checked) return;
              setMode("android");
              setCopied(false);
              setCopyError(false);
            }} />
            Android 应用（推荐）
          </label>
          <label className="pairing-mode-option">
            <input type="radio" name="pairing-mode" checked={mode === "browser"} onChange={event => {
              if (!event.currentTarget.checked) return;
              setMode("browser");
              setCopied(false);
              setCopyError(false);
            }} />
            浏览器（仅 HTTPS）
          </label>
        </div>
      </fieldset>
      <p className="pairing-mode-help">
        {mode === "android"
          ? `将生成 Android 专用二维码，应用连接地址：${siteOrigin}`
          : "浏览器配对必须使用 HTTPS，以安全生成并保存设备密钥。"}
      </p>
      <label className="pairing-origin">新设备访问地址
        <input className="input" type="url" value={siteOrigin} onChange={event => {
          setSiteOrigin(event.target.value);
          setOriginEdited(true);
          setCopied(false);
          setCopyError(false);
        }} />
      </label>
      {pairingOrigins.isError && mode === "android" && <p className="inline-alert" role="alert">
        无法自动识别局域网地址，请填写手机可访问的电脑地址。
      </p>}
      {pairTarget.error && <p className="inline-alert" role="alert">{pairTarget.error}</p>}
      {pairTarget.url && <div className="pairing-qr"><QRCodeSVG value={pairTarget.url} size={228} level="M" marginSize={2} /></div>}
      <p className="pairing-expiry">二维码两分钟内有效，且只能使用一次。</p>
      {mode === "browser" && !window.isSecureContext && <p className="inline-alert" role="alert">当前页面不是安全上下文。浏览器配对需要 HTTPS，请先为网站启用 HTTPS。</p>}
      <button className="btn-outline pairing-copy" type="button" disabled={!pairTarget.url} onClick={async () => {
        try {
          await navigator.clipboard.writeText(pairTarget.url);
          setCopied(true); setCopyError(false);
        } catch { setCopyError(true); }
      }}>{copied ? "链接已复制" : "复制配对链接"}</button>
      {copyError && <p className="inline-alert" role="alert">浏览器无法复制链接，请直接使用相机扫描二维码。</p>}
    </>}
    {pending && <div className="pairing-request">
      <p>设备 <strong>{data.requested_device?.name}</strong> 正在申请加入。确认两台设备显示相同数字后再允许。</p>
      <strong className="device-verification-code">{data.verification_code?.slice(0, 3)} {data.verification_code?.slice(3)}</strong>
      <div className="pairing-actions">
        <button className="btn-outline" type="button" disabled={decide.isPending} onClick={() => decide.mutate("reject")}>拒绝</button>
        <button className="btn-primary" type="button" disabled={decide.isPending} onClick={() => decide.mutate("approve")}>允许此设备</button>
      </div>
    </div>}
    {data.status === "approved" && <div className="pairing-result"><Icon name="check" size={26} /><div><strong>设备已加入</strong><p>新设备现在可以安全访问本站。</p></div></div>}
    {data.status === "rejected" && <p className="inline-alert">已拒绝这次设备添加请求。</p>}
    {data.status === "expired" && <p className="inline-alert">二维码已经过期，请关闭后重新生成。</p>}
    {(current.isError || decide.isError) && <p className="inline-alert" role="alert">{current.error?.message ?? decide.error?.message}</p>}
  </section>;
}

export default function TrustedDevicesPage() {
  const qc = useQueryClient();
  const [pairing, setPairing] = useState<Pairing | null>(null);
  const [dismissedPairingId, setDismissedPairingId] = useState<number | null>(null);
  const devices = useQuery({ queryKey: ["trusted-devices"], queryFn: listTrustedDevices });
  const pendingPairings = useQuery({
    queryKey: ["pending-device-pairings"],
    queryFn: listPendingPairings,
    refetchInterval: 3000,
  });
  useEffect(() => {
    const resumable = pendingPairings.data?.items[0];
    if (!pairing && resumable && resumable.id !== dismissedPairingId) setPairing(resumable);
  }, [dismissedPairingId, pairing, pendingPairings.data]);
  const create = useMutation({
    mutationFn: createPairing,
    onSuccess: data => { setDismissedPairingId(null); setPairing(data); },
  });
  const revoke = useMutation({
    mutationFn: revokeTrustedDevice,
    onSuccess: async (_, id) => {
      const current = devices.data?.items.find(device => device.id === id)?.is_current;
      if (current) { window.location.reload(); return; }
      await qc.invalidateQueries({ queryKey: ["trusted-devices"] });
    },
  });

  return <div className="trusted-devices">
    <section className="card trusted-devices-intro">
      <div><h2>只允许你确认过的设备</h2><p>每台设备拥有独立的浏览器私钥。扫码只会发起申请，必须在已有信任设备上核对验证码并批准。</p></div>
      <button className="btn-primary" type="button" disabled={create.isPending} onClick={() => create.mutate()}>
        <Icon name="plus" size={16} />{create.isPending ? "正在生成…" : "添加信任设备"}
      </button>
    </section>
    {create.isError && <p className="inline-alert" role="alert">{create.error.message}</p>}
    {pairing && <PairingPanel pairing={pairing} close={() => { setDismissedPairingId(pairing.id); setPairing(null); }} />}
    <section className="card trusted-device-list">
      <div className="trusted-device-list-heading"><h2>已信任设备</h2><span>{devices.data?.items.filter(item => item.status === "active").length ?? 0} 台</span></div>
      {devices.isLoading && <p role="status">正在读取设备列表…</p>}
      {devices.isError && <p className="inline-alert" role="alert">{devices.error.message}<button className="btn-ghost" onClick={() => devices.refetch()}>重试</button></p>}
      {devices.data?.items.map(device => <article className="trusted-device-row" key={device.id}>
        <div className="trusted-device-icon"><Icon name="storage" size={20} /></div>
        <div className="trusted-device-copy">
          <div><strong>{device.name}</strong>{device.is_current && <span>当前设备</span>}{device.is_bootstrap && <span>初始设备</span>}{device.status === "pending" && <span>等待批准</span>}</div>
          <p>加入于 {formatTime(device.approved_at ?? device.created_at)} · 最近使用 {formatTime(device.last_seen_at)}</p>
        </div>
        {device.status === "active" && <button className="btn-ghost trusted-device-revoke" type="button" disabled={revoke.isPending}
          onClick={() => {
            if (window.confirm(`确定撤销“${device.name}”吗？该设备将立即失去访问权限。`)) revoke.mutate(device.id);
          }}>撤销</button>}
      </article>)}
      {revoke.isError && <p className="inline-alert" role="alert">{revoke.error.message}</p>}
    </section>
    <p className="trusted-device-note">私钥只保存在对应浏览器中且不可导出。清除该站点的浏览器数据后，需要重新扫码添加；丢失设备时，请在这里立即撤销。</p>
  </div>;
}
