import { useEffect, useState } from "react";
import { getPairClaimStatus, requestDevicePairing, type PairClaim } from "../api/deviceAuth";
import {
  authenticateDevice,
  clearDeviceIdentity,
  createDeviceKey,
  readDeviceIdentity,
  saveDeviceIdentity,
  suggestedDeviceName,
} from "../lib/deviceIdentity";
import Icon from "../components/Icon";

export default function PairDevicePage() {
  const [token] = useState(() => {
    const value = new URLSearchParams(location.hash.slice(1)).get("token") || "";
    history.replaceState(null, "", "/pair");
    return value;
  });
  const [name, setName] = useState(suggestedDeviceName);
  const [claim, setClaim] = useState<PairClaim | null>(null);
  const [existing, setExisting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    void readDeviceIdentity().then(value => {
      setExisting(Boolean(value));
      if (value?.pendingClaim) setClaim(value.pendingClaim);
    });
  }, []);
  useEffect(() => {
    if (!claim) return;
    const timer = window.setInterval(async () => {
      try {
        const state = await getPairClaimStatus(claim.pairing_id, claim.claim_secret);
        if (state.status === "approved") {
          window.clearInterval(timer);
          const identity = await readDeviceIdentity();
          if (!identity) throw new Error("设备密钥已经丢失，请重新扫码");
          const approvedIdentity = { ...identity };
          delete approvedIdentity.pendingClaim;
          await saveDeviceIdentity(approvedIdentity);
          await authenticateDevice(approvedIdentity);
          location.href = "/";
        } else if (["rejected", "expired"].includes(state.status)) {
          window.clearInterval(timer);
          await clearDeviceIdentity();
          setError(state.status === "rejected" ? "添加请求已被拒绝" : "二维码已经过期，请重新生成");
          setClaim(null);
        }
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "无法读取配对状态");
      }
    }, 1500);
    return () => window.clearInterval(timer);
  }, [claim]);
  const submit = async () => {
    try {
      setBusy(true); setError("");
      const generated = await createDeviceKey(name.trim());
      const result = await requestDevicePairing(token, { name: generated.name, public_key: generated.public_key });
      await saveDeviceIdentity({ ...generated, deviceId: result.device_id, pendingClaim: result });
      setClaim(result);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "配对申请失败");
    } finally { setBusy(false); }
  };
  return <main className="device-access-page"><section className="device-access-card card">
    <div className="device-access-mark"><Icon name="plus" size={28} /></div>
    <p className="eyebrow"><span /> DEVICE PAIRING · 添加设备</p>
    <h1>{claim ? "等待已有设备确认" : "添加为信任设备"}</h1>
    {!token && !claim ? <p className="inline-alert" role="alert">二维码中没有有效的配对令牌，请重新扫描。</p> : claim ? <>
      <p>请确认新旧设备显示相同的六位数字，然后在已有设备上选择“允许”。</p>
      <strong className="device-verification-code">{claim.verification_code.slice(0, 3)} {claim.verification_code.slice(3)}</strong>
      <p className="device-waiting"><span />正在等待批准…</p>
    </> : existing ? <>
      <p>这个浏览器已经保存了一把设备密钥。为避免覆盖现有身份，请使用尚未受信任的浏览器扫描二维码。</p>
      <a className="btn-outline" href="/">返回首页</a>
    </> : <>
      <p>此设备会在浏览器中生成不可导出的私钥。已有信任设备批准后才能访问影像内容。</p>
      <label>设备名称<input className="input" value={name} maxLength={80} onChange={event => setName(event.target.value)} /></label>
      {error && <p className="inline-alert" role="alert">{error}</p>}
      <button className="btn-primary" disabled={busy || !name.trim()} onClick={submit}>{busy ? "正在生成设备密钥…" : "申请添加设备"}</button>
    </>}
    {error && claim && <p className="inline-alert" role="alert">{error}</p>}
  </section></main>;
}
