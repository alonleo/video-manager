import { useEffect, useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { bootstrapDevice, getDeviceAuthStatus, type DeviceAuthStatus } from "../api/deviceAuth";
import {
  authenticateDevice,
  createDeviceKey,
  readDeviceIdentity,
  saveDeviceIdentity,
  suggestedDeviceName,
} from "../lib/deviceIdentity";
import PairDevicePage from "../pages/PairDevicePage";
import Icon from "./Icon";
import "./device-access.css";

function AccessFrame({ children }: { children: ReactNode }) {
  return <main className="device-access-page"><section className="device-access-card card">
    <div className="device-access-mark"><Icon name="storage" size={28} /></div>
    <p className="eyebrow"><span /> TRUSTED DEVICE · 设备信任</p>
    {children}
  </section></main>;
}

function Bootstrap({ onReady }: { onReady: () => void }) {
  const [name, setName] = useState(suggestedDeviceName);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async () => {
    try {
      setBusy(true); setError("");
      const generated = await createDeviceKey(name.trim());
      const device = await bootstrapDevice({ name: generated.name, public_key: generated.public_key });
      await saveDeviceIdentity({ ...generated, deviceId: device.id });
      onReady();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "初始化失败");
    } finally { setBusy(false); }
  };
  return <AccessFrame>
    <h1>设置首台信任设备</h1>
    <p>只有服务器本机可以完成这一步。以后添加设备，需要由已信任设备显示二维码并确认。</p>
    <label>设备名称<input className="input" value={name} maxLength={80} onChange={event => setName(event.target.value)} /></label>
    {error && <p className="inline-alert" role="alert">{error}</p>}
    <button className="btn-primary" disabled={busy || !name.trim()} onClick={submit}>{busy ? "正在建立密钥…" : "信任此设备"}</button>
  </AccessFrame>;
}

function Denied({ status, retry }: { status: DeviceAuthStatus; retry: () => void }) {
  return <AccessFrame>
    <h1>{status.initialized ? "此设备尚未受信任" : "等待本机初始化"}</h1>
    <p>{status.initialized
      ? "请在一台已信任设备中打开“设置 → 信任设备”，生成二维码后使用此设备扫描。"
      : "请先在服务器本机浏览器中打开本站，完成首台信任设备初始化。"}</p>
    <button className="btn-outline" onClick={retry}><Icon name="refresh" size={16} />重新检查</button>
    <Link className="device-access-help" to="/pair">已经扫描二维码？继续配对</Link>
  </AccessFrame>;
}

export default function DeviceAccessGate({ children }: { children: ReactNode }) {
  const location = useLocation();
  const [status, setStatus] = useState<DeviceAuthStatus | null>(null);
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState("");
  const refresh = async () => {
    setChecking(true); setError("");
    try {
      let next = await getDeviceAuthStatus();
      if (next.initialized && !next.authenticated) {
        const identity = await readDeviceIdentity().catch(() => null);
        if (identity) {
          try { await authenticateDevice(identity); next = await getDeviceAuthStatus(); } catch { /* revoked or stale identity */ }
        }
      }
      setStatus(next);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法连接到服务器");
    } finally { setChecking(false); }
  };
  useEffect(() => { void refresh(); }, []);
  if (location.pathname === "/pair") return <PairDevicePage />;
  if (checking) return <AccessFrame><h1>正在验证设备…</h1><p>正在读取此浏览器的设备密钥。</p></AccessFrame>;
  if (!status) return <AccessFrame><h1>暂时无法验证设备</h1><p>{error || "服务器没有返回设备状态。"}</p><button className="btn-outline" onClick={refresh}><Icon name="refresh" size={16} />重试</button></AccessFrame>;
  if (!status.initialized && status.bootstrap_allowed) return <Bootstrap onReady={refresh} />;
  if (!status.authenticated) return <Denied status={status} retry={refresh} />;
  return children;
}
