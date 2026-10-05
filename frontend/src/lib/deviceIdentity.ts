import {
  createDeviceChallenge,
  verifyDeviceSignature,
  type DeviceRegistration,
  type PairClaim,
} from "../api/deviceAuth";

const DATABASE = "frame-archive-device-identity";
const STORE = "identity";
const CURRENT = "current";

export type DeviceIdentity = DeviceRegistration & {
  deviceId: number;
  privateKey: CryptoKey;
  pendingClaim?: PairClaim;
};

function database(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(STORE);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export async function readDeviceIdentity(): Promise<DeviceIdentity | null> {
  const db = await database();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction(STORE, "readonly");
    const request = transaction.objectStore(STORE).get(CURRENT);
    request.onsuccess = () => resolve((request.result as DeviceIdentity | undefined) ?? null);
    request.onerror = () => reject(request.error);
    transaction.oncomplete = () => db.close();
  });
}

export async function saveDeviceIdentity(identity: DeviceIdentity): Promise<void> {
  const db = await database();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction(STORE, "readwrite");
    transaction.objectStore(STORE).put(identity, CURRENT);
    transaction.oncomplete = () => { db.close(); resolve(); };
    transaction.onerror = () => reject(transaction.error);
  });
}

export async function clearDeviceIdentity(): Promise<void> {
  const db = await database();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction(STORE, "readwrite");
    transaction.objectStore(STORE).delete(CURRENT);
    transaction.oncomplete = () => { db.close(); resolve(); };
    transaction.onerror = () => reject(transaction.error);
  });
}

export async function createDeviceKey(name: string): Promise<Omit<DeviceIdentity, "deviceId">> {
  if (!window.isSecureContext || !window.crypto?.subtle || !window.indexedDB)
    throw new Error("设备注册需要 HTTPS 安全连接；服务器本机可使用 localhost。");
  const pair = await crypto.subtle.generateKey(
    { name: "ECDSA", namedCurve: "P-256" },
    false,
    ["sign", "verify"],
  );
  const publicKey = await crypto.subtle.exportKey("jwk", pair.publicKey);
  return { name, public_key: publicKey, privateKey: pair.privateKey };
}

function decodeChallenge(value: string) {
  const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
  const bytes = atob(normalized + "=".repeat((4 - normalized.length % 4) % 4));
  return Uint8Array.from(bytes, character => character.charCodeAt(0));
}

function encodeSignature(value: ArrayBuffer) {
  const bytes = new Uint8Array(value);
  let binary = "";
  bytes.forEach(byte => { binary += String.fromCharCode(byte); });
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export async function authenticateDevice(identity: DeviceIdentity) {
  const { challenge } = await createDeviceChallenge(identity.deviceId);
  const signature = await crypto.subtle.sign(
    { name: "ECDSA", hash: "SHA-256" },
    identity.privateKey,
    decodeChallenge(challenge),
  );
  return verifyDeviceSignature(identity.deviceId, challenge, encodeSignature(signature));
}

export function suggestedDeviceName() {
  const platform = navigator.platform || "浏览器";
  const mobile = /Android|iPhone|iPad/i.test(navigator.userAgent);
  return mobile ? `移动设备 · ${platform}` : `电脑 · ${platform}`;
}
