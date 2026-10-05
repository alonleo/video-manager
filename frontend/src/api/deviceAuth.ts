import { request } from "./client";

export type DeviceRegistration = { name: string; public_key: JsonWebKey };

export type TrustedDevice = {
  id: number;
  name: string;
  status: "active" | "pending" | "revoked";
  is_bootstrap: boolean;
  is_current: boolean;
  created_at: string;
  approved_at: string | null;
  last_seen_at: string | null;
};

export type DeviceAuthStatus = {
  initialized: boolean;
  authenticated: boolean;
  bootstrap_allowed: boolean;
  device: TrustedDevice | null;
};

export type Pairing = {
  id: number;
  token?: string;
  status: "open" | "pending" | "approved" | "rejected" | "expired";
  expires_at: string;
  verification_code?: string | null;
  requested_device?: TrustedDevice | null;
};

export type PairClaim = {
  pairing_id: number;
  device_id: number;
  claim_secret: string;
  verification_code: string;
  status: string;
};

export const getDeviceAuthStatus = () => request<DeviceAuthStatus>("/device-auth/status");
export const bootstrapDevice = (body: DeviceRegistration) =>
  request<TrustedDevice>("/device-auth/bootstrap", { method: "POST", body: JSON.stringify(body) });
export const createDeviceChallenge = (deviceId: number) =>
  request<{ challenge: string; expires_in: number }>("/device-auth/challenge", {
    method: "POST", body: JSON.stringify({ device_id: deviceId }),
  });
export const verifyDeviceSignature = (deviceId: number, challenge: string, signature: string) =>
  request<TrustedDevice>("/device-auth/verify", {
    method: "POST", body: JSON.stringify({ device_id: deviceId, challenge, signature }),
  });
export const requestDevicePairing = (token: string, body: DeviceRegistration) =>
  request<PairClaim>("/device-auth/pair", { method: "POST", body: JSON.stringify({ token, ...body }) });
export const getPairClaimStatus = (pairingId: number, claimSecret: string) =>
  request<{ status: Pairing["status"]; device_id: number }>("/device-auth/pair-status", {
    method: "POST", body: JSON.stringify({ pairing_id: pairingId, claim_secret: claimSecret }),
  });
export const listTrustedDevices = () => request<{ items: TrustedDevice[] }>("/trusted-devices");
export const listPendingPairings = () => request<{ items: Pairing[] }>("/trusted-devices/pairings");
export const createPairing = () => request<Pairing>("/trusted-devices/pairings", { method: "POST" });
export const getPairing = (id: number) => request<Pairing>(`/trusted-devices/pairings/${id}`);
export const decidePairing = (id: number, decision: "approve" | "reject") =>
  request<Pairing>(`/trusted-devices/pairings/${id}/${decision}`, { method: "POST" });
export const renameTrustedDevice = (id: number, name: string) =>
  request<TrustedDevice>(`/trusted-devices/${id}`, { method: "PATCH", body: JSON.stringify({ name }) });
export const revokeTrustedDevice = (id: number) =>
  request<void>(`/trusted-devices/${id}`, { method: "DELETE" });
