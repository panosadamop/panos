/** WebAuthn JSON ↔ browser API (base64url fields). The hub sends py_webauthn `options_to_json`. */

function fromB64url(value: string): ArrayBuffer {
  const b64 = value.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((value.length + 3) % 4);
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes.buffer;
}

function toB64url(buffer: ArrayBuffer | null): string {
  if (!buffer) return "";
  let bin = "";
  for (const b of new Uint8Array(buffer)) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

type Json = Record<string, unknown>;

export function supported(): boolean {
  return typeof window !== "undefined" && "PublicKeyCredential" in window && !!navigator.credentials;
}

export async function createPasskey(optionsJson: string): Promise<Json> {
  const o = JSON.parse(optionsJson);
  const publicKey: PublicKeyCredentialCreationOptions = {
    ...o,
    challenge: fromB64url(o.challenge),
    user: { ...o.user, id: fromB64url(o.user.id) },
    excludeCredentials: (o.excludeCredentials ?? []).map((c: Json) => ({ ...c, id: fromB64url(c.id as string) })),
  };
  const cred = (await navigator.credentials.create({ publicKey })) as PublicKeyCredential | null;
  if (!cred) throw new Error("no passkey was created");
  const r = cred.response as AuthenticatorAttestationResponse;
  return {
    id: cred.id,
    rawId: toB64url(cred.rawId),
    type: cred.type,
    response: {
      clientDataJSON: toB64url(r.clientDataJSON),
      attestationObject: toB64url(r.attestationObject),
      transports: r.getTransports?.() ?? [],
    },
    clientExtensionResults: cred.getClientExtensionResults(),
  };
}

export async function getPasskey(optionsJson: string): Promise<Json> {
  const o = JSON.parse(optionsJson);
  const publicKey: PublicKeyCredentialRequestOptions = {
    ...o,
    challenge: fromB64url(o.challenge),
    allowCredentials: (o.allowCredentials ?? []).map((c: Json) => ({ ...c, id: fromB64url(c.id as string) })),
  };
  const cred = (await navigator.credentials.get({ publicKey })) as PublicKeyCredential | null;
  if (!cred) throw new Error("no passkey was used");
  const r = cred.response as AuthenticatorAssertionResponse;
  return {
    id: cred.id,
    rawId: toB64url(cred.rawId),
    type: cred.type,
    response: {
      clientDataJSON: toB64url(r.clientDataJSON),
      authenticatorData: toB64url(r.authenticatorData),
      signature: toB64url(r.signature),
      userHandle: toB64url(r.userHandle),
    },
    clientExtensionResults: cred.getClientExtensionResults(),
  };
}
