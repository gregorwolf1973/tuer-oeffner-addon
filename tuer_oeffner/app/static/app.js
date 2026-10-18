/* Haustuer - die App fuer Freunde. Ohne Bibliotheken, ohne Build. */
"use strict";

const $ = (sel, wurzel = document) => wurzel.querySelector(sel);
const $$ = (sel, wurzel = document) => [...wurzel.querySelectorAll(sel)];
let zustand = {};

/* ---------- Darstellung: dunkel (Voreinstellung), hell, wie das Handy ---------- */

const THEMEN = { dunkel: "#11161c", hell: "#f4f6f9", auto: null };

function themaSetzen(wahl) {
  if (!(wahl in THEMEN)) wahl = "dunkel";
  document.documentElement.dataset.theme = wahl;
  const dunkel = wahl === "dunkel" || (wahl === "auto" && matchMedia("(prefers-color-scheme: dark)").matches);
  $('meta[name="theme-color"]').content = dunkel ? THEMEN.dunkel : THEMEN.hell;
  $$("[data-theme-wahl]").forEach((k) => k.setAttribute("aria-pressed", String(k.dataset.themeWahl === wahl)));
}

function themaGespeichert() {
  try { return localStorage.getItem("thema") || "dunkel"; } catch { return "dunkel"; }
}

themaSetzen(themaGespeichert());
$$("[data-theme-wahl]").forEach((k) => k.addEventListener("click", () => {
  try { localStorage.setItem("thema", k.dataset.themeWahl); } catch { /* nur fuer diese Sitzung */ }
  themaSetzen(k.dataset.themeWahl);
}));
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => themaSetzen(themaGespeichert()));

/* ---------- Server ---------- */

async function api(pfad, daten) {
  const opt = { headers: { "X-Tuer": "1" }, credentials: "same-origin" };
  if (daten !== undefined) {
    opt.method = "POST";
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(daten);
  }
  let antwort;
  try {
    antwort = await fetch("api/" + pfad, opt);
  } catch {
    throw new Error("Keine Verbindung. Bitte Netz prüfen.");
  }
  const inhalt = await antwort.json().catch(() => ({}));
  if (!antwort.ok) throw new Error(inhalt.fehler || "Fehler " + antwort.status);
  return inhalt;
}

/* ---------- Anzeige ---------- */

function zeige(name) {
  $$("[data-ansicht]").forEach((s) => (s.hidden = s.dataset.ansicht !== name));
}

let meldungUhr;
function melde(text, art = "fehler") {
  const m = $("#meldung");
  m.textContent = text;
  m.className = "meldung " + art;
  m.hidden = !text;
  clearTimeout(meldungUhr);
  if (text && art === "ok") meldungUhr = setTimeout(() => (m.hidden = true), 6000);
}

function datum(sek) {
  return sek ? new Date(sek * 1000).toLocaleDateString("de-DE") : "–";
}

async function laden() {
  try {
    zustand = await api("status");
  } catch (e) {
    melde(e.message);
    return;
  }
  const passkey = zustand.passkey_moeglich && window.PublicKeyCredential;
  $("#passkey-anmelden").hidden = !passkey;
  $("#passkey-trenner").hidden = !passkey;
  $("#zur-registrierung").hidden = !zustand.registrierung_offen;

  if (!zustand.angemeldet) return zeige("anmelden");
  if (zustand.stufe === "einrichten") return einrichtenZeigen(false);
  if (zustand.stufe === "pw") return zeige("code");

  $("#name").textContent = zustand.name;
  $$(".konto-benutzer").forEach((i) => (i.value = zustand.benutzername || zustand.email));
  $("#zwei-faktor-text").textContent = zustand.zwei_faktor
    ? (zustand.zwei_faktor_pflicht
      ? "Eingeschaltet. Bei der Anmeldung mit Passwort wird ein Code verlangt – das ist hier Pflicht."
      : "Eingeschaltet. Zum Ausschalten Passwort und einen aktuellen Code eingeben.")
    : "Ausgeschaltet. Mit Authenticator-App ist dein Zugang besser geschützt, falls jemand dein Passwort erfährt.";
  $("#zwei-faktor-an").hidden = zustand.zwei_faktor;
  $("#form-zwei-faktor-aus").hidden = !zustand.zwei_faktor || zustand.zwei_faktor_pflicht;
  $("#wartet").hidden = zustand.status === "aktiv";
  $("#oeffnen").hidden = zustand.status !== "aktiv";
  $("#fern").hidden = !zustand.fern;
  $("#warmwasser").hidden = !zustand.warmwasser;
  $("#staubsauger").hidden = !zustand.staubsauger;
  familieLaden();
  $("#radius-hinweis").textContent = `Funktioniert bis ${zustand.radius} m von der Haustür – dein Standort wird dafür geprüft.`;
  $("#passkeys-bereich").hidden = !passkey;
  const liste = $("#passkeys");
  liste.replaceChildren(...(zustand.passkeys || []).map((p) => {
    const li = document.createElement("li");
    li.textContent = `${p.name} · angelegt ${datum(p.angelegt)} · zuletzt ${datum(p.zuletzt)} `;
    const weg = document.createElement("button");
    weg.type = "button";
    weg.className = "knopf klein";
    weg.textContent = "Entfernen";
    weg.onclick = async () => {
      if (!confirm(`Passkey „${p.name}“ entfernen?`)) return;
      await ausfuehren(() => api("passkey/loeschen", { id: p.id }));
      laden();
    };
    li.append(weg);
    return li;
  }));
  zeige("haupt");
}

/* konto: aus "Konto" heraus eingeschaltet (sonst Pflicht nach der Anmeldung) */
let einrichtenAusKonto = false;

async function einrichtenZeigen(konto) {
  einrichtenAusKonto = konto;
  $("#qr").replaceChildren();
  zeige("einrichten");
  try {
    const d = konto ? await api("2fa/einschalten/start", {}) : await api("2fa/einrichten");
    // Das SVG kommt von unserem eigenen Server (segno), kein Fremdinhalt
    $("#qr").innerHTML = d.svg;
    $("#totp-geheimnis").textContent = d.geheimnis.replace(/(.{4})/g, "$1 ").trim();
    $("#totp-link").href = d.uri;
  } catch (e) {
    melde(e.message);
  }
}

async function ausfuehren(fn) {
  melde("");
  try {
    return await fn();
  } catch (e) {
    melde(e.message);
    throw e;
  }
}

function formular(id, fn) {
  $(id).addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const f = ev.target;
    const knopf = $("button", f);
    knopf.disabled = true;
    try {
      await ausfuehren(() => fn(Object.fromEntries(new FormData(f))));
      f.reset();
    } catch { /* Meldung steht schon */ }
    knopf.disabled = false;
  });
}

/* ---------- Formulare ---------- */

formular("#form-anmelden", async (d) => {
  await api("anmelden", d);
  await laden();
});

formular("#form-registrieren", async (d) => {
  if (d.passwort !== d.passwort2) throw new Error("Die beiden Passwörter sind nicht gleich.");
  await api("registrieren", { name: d.name, benutzername: d.benutzername, email: d.email, passwort: d.passwort });
  await laden();
});

formular("#form-einrichten", async (d) => {
  const ausKonto = einrichtenAusKonto;
  await api(ausKonto ? "2fa/einschalten" : "2fa/bestaetigen", d);
  await laden();
  if (ausKonto) melde("Zwei-Faktor-Anmeldung ist eingeschaltet. Andere Geräte sind abgemeldet.", "ok");
  else if (zustand.status === "wartet") melde("Eingerichtet. Jetzt muss dein Zugang noch freigegeben werden.", "ok");
  else melde("Zwei-Faktor-Anmeldung ist eingerichtet.", "ok");
});

formular("#form-zwei-faktor-aus", async (d) => {
  await api("2fa/ausschalten", { passwort: d.passwort, code: d.code });
  melde("Zwei-Faktor-Anmeldung ist ausgeschaltet.", "ok");
  await laden();
});

$("#zwei-faktor-an").addEventListener("click", () => einrichtenZeigen(true));

formular("#form-code", async (d) => {
  await api("anmelden/code", d);
  await laden();
});

formular("#form-passwort", async (d) => {
  await api("passwort", d);
  melde("Passwort geändert. Andere Geräte sind abgemeldet.", "ok");
});

document.addEventListener("click", async (ev) => {
  const geh = ev.target.closest("[data-geh]");
  if (geh) {
    ev.preventDefault();
    melde("");
    zeige(geh.dataset.geh);
  }
  const aktion = ev.target.closest("[data-aktion]")?.dataset.aktion;
  if (aktion === "abmelden") {
    ev.preventDefault();
    await api("abmelden", {}).catch(() => {});
    melde("");
    laden();
  }
  if (aktion === "einrichten-abbrechen") {
    ev.preventDefault();
    melde("");
    if (einrichtenAusKonto) laden();
    else await api("abmelden", {}).catch(() => {}).then(laden);
  }
  if (aktion === "neu-laden") laden();
});

/* ---------- Passkeys (WebAuthn) ---------- */

const zuBytes = (b64url) => {
  const b64 = b64url.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(b64url.length / 4) * 4, "=");
  return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)).buffer;
};
const zuText = (puffer) => {
  if (!puffer) return null;
  let s = "";
  new Uint8Array(puffer).forEach((b) => (s += String.fromCharCode(b)));
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
};

$("#passkey-neu").addEventListener("click", () => ausfuehren(async () => {
  const o = await api("passkey/registrieren/start", {});
  o.challenge = zuBytes(o.challenge);
  o.user.id = zuBytes(o.user.id);
  (o.excludeCredentials || []).forEach((c) => (c.id = zuBytes(c.id)));
  let cred;
  try {
    cred = await navigator.credentials.create({ publicKey: o });
  } catch {
    throw new Error("Abgebrochen oder auf diesem Gerät nicht möglich.");
  }
  const name = prompt("Name für diesen Passkey (z. B. „Handy“):", "Handy") || "Passkey";
  await api("passkey/registrieren/ende", {
    name,
    credential: {
      id: cred.id,
      rawId: zuText(cred.rawId),
      type: cred.type,
      response: {
        clientDataJSON: zuText(cred.response.clientDataJSON),
        attestationObject: zuText(cred.response.attestationObject),
        transports: cred.response.getTransports ? cred.response.getTransports() : [],
      },
      clientExtensionResults: cred.getClientExtensionResults(),
      authenticatorAttachment: cred.authenticatorAttachment || undefined,
    },
  });
  melde("Passkey angelegt. Ab jetzt reicht „Mit Passkey anmelden“.", "ok");
  laden();
}).catch(() => {}));

$("#passkey-anmelden").addEventListener("click", () => ausfuehren(async () => {
  const o = await api("passkey/anmelden/start", {});
  o.challenge = zuBytes(o.challenge);
  (o.allowCredentials || []).forEach((c) => (c.id = zuBytes(c.id)));
  let cred;
  try {
    cred = await navigator.credentials.get({ publicKey: o });
  } catch {
    throw new Error("Abgebrochen oder kein passender Passkey gefunden.");
  }
  await api("passkey/anmelden/ende", {
    credential: {
      id: cred.id,
      rawId: zuText(cred.rawId),
      type: cred.type,
      response: {
        clientDataJSON: zuText(cred.response.clientDataJSON),
        authenticatorData: zuText(cred.response.authenticatorData),
        signature: zuText(cred.response.signature),
        userHandle: zuText(cred.response.userHandle),
      },
      clientExtensionResults: cred.getClientExtensionResults(),
      authenticatorAttachment: cred.authenticatorAttachment || undefined,
    },
  });
  laden();
}).catch(() => {}));

/* ---------- Oeffnen ---------- */

function standort() {
  return new Promise((ok, nein) => {
    if (!navigator.geolocation) return nein(new Error("Dieses Gerät kann keinen Standort liefern."));
    navigator.geolocation.getCurrentPosition(
      (p) => ok({ lat: p.coords.latitude, lon: p.coords.longitude, genauigkeit: p.coords.accuracy }),
      (f) => nein(new Error(f.code === 1
        ? "Ohne Standortfreigabe geht es nicht. Bitte in den Browser-Einstellungen erlauben."
        : "Standort nicht ermittelt. Bitte noch einmal versuchen.")),
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 },
    );
  });
}

async function oeffnen(modus, schieber) {
  schieber.classList.add("arbeitet");
  $(".spur-text", schieber).textContent = modus === "fern" ? "Öffne …" : "Standort wird geprüft …";
  try {
    const daten = modus === "fern" ? { modus } : { modus, ...(await standort()) };
    $(".spur-text", schieber).textContent = "Öffne …";
    await api("oeffnen", daten);
    schieber.classList.add("offen");
    $(".spur-text", schieber).textContent = "Tür ist offen";
    if (navigator.vibrate) navigator.vibrate(80);
    melde("Die Tür ist offen.", "ok");
  } catch (e) {
    melde(e.message);
  }
  setTimeout(() => schieberZurueck(schieber), 2500);
}

function schieberZurueck(schieber) {
  schieber.classList.remove("arbeitet", "offen");
  const griff = $(".griff", schieber);
  griff.style.transform = "";
  $(".spur-text", schieber).textContent =
    schieber.dataset.modus === "fern" ? "Aus der Ferne öffnen" : "Zum Öffnen schieben";
}

/* Wischen statt Tippen: so oeffnet die Hosentasche nichts aus Versehen. */
$$(".schieber").forEach((schieber) => {
  const spur = $(".spur", schieber);
  const griff = $(".griff", schieber);
  let start = null;
  let weg = 0;
  const max = () => spur.clientWidth - griff.offsetWidth - 8;

  griff.addEventListener("pointerdown", (ev) => {
    if (schieber.classList.contains("arbeitet")) return;
    start = ev.clientX;
    weg = 0;
    try { griff.setPointerCapture(ev.pointerId); } catch { /* Zeiger schon weg */ }
    schieber.classList.add("zieht");
  });
  griff.addEventListener("pointermove", (ev) => {
    if (start === null) return;
    weg = Math.max(0, Math.min(max(), ev.clientX - start));
    griff.style.transform = `translateX(${weg}px)`;
  });
  const loslassen = () => {
    if (start === null) return;
    start = null;
    schieber.classList.remove("zieht");
    if (weg >= max() * 0.92) {
      griff.style.transform = `translateX(${max()}px)`;
      oeffnen(schieber.dataset.modus, schieber);
    } else {
      griff.style.transform = "";
    }
  };
  griff.addEventListener("pointerup", loslassen);
  griff.addEventListener("pointercancel", loslassen);
  // Tastatur: bewusst Enter UND Bestaetigung - eine Taste allein reicht nicht
  griff.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !schieber.classList.contains("arbeitet")
        && confirm(schieber.dataset.modus === "fern" ? "Tür aus der Ferne öffnen?" : "Tür öffnen?")) {
      oeffnen(schieber.dataset.modus, schieber);
    }
  });
});

/* ---------- Warmwasser ---------- */

let wwStartet = false;

function warmwasserZeigen(d) {
  const temp = $("#ww-temperatur");
  temp.textContent = d.temperatur == null
    ? "–"
    : `${String(d.temperatur).replace(".", ",")} ${d.einheit}`;
  const knopf = $("#ww-aufheizen");
  knopf.disabled = wwStartet || d.laeuft === true;
  knopf.textContent = d.laeuft ? "Heizt gerade auf …" : "Wasser aufheizen";
  $("#ww-status").textContent = d.laeuft
    ? "Das Wasser wird gerade aufgeheizt."
    : "Temperatur im Speicher";
}

async function warmwasserLaden() {
  try {
    warmwasserZeigen(await api("warmwasser"));
  } catch { /* Anzeige bleibt, wie sie war */ }
}

$("#ww-aufheizen").addEventListener("click", async () => {
  const knopf = $("#ww-aufheizen");
  wwStartet = true;
  knopf.disabled = true;
  knopf.textContent = "Starte …";
  try {
    await ausfuehren(() => api("warmwasser/aufheizen", {}));
    if (navigator.vibrate) navigator.vibrate(80);
    melde("Das Wasser wird aufgeheizt.", "ok");
    knopf.textContent = "Heizt gerade auf …";
    $("#ww-status").textContent = "Gestartet. Die Wärmepumpe meldet sich gleich.";
    // Die Waermepumpe meldet die Ladung erst nach einer Weile
    setTimeout(() => { wwStartet = false; warmwasserLaden(); }, 60000);
  } catch {
    wwStartet = false;
    warmwasserLaden();
  }
});

/* ---------- Staubsauger ---------- */

const SAUGER_TEXT = {
  cleaning: "Saugt gerade", docked: "In der Station", returning: "Fährt zur Station",
  idle: "Steht bereit", paused: "Pausiert", error: "Hat einen Fehler – bitte nachsehen",
};

function staubsaugerZeigen(d) {
  $("#sg-batterie").textContent = d.batterie == null ? "–" : `🔋 ${d.batterie} %`;
  $("#sg-status").textContent = d.zustand == null
    ? "Gerade nicht erreichbar"
    : SAUGER_TEXT[d.zustand] || d.zustand;
  $('[data-saug="start"]').disabled = d.zustand === "cleaning";
  $('[data-saug="zur_station"]').disabled = d.zustand === "docked" || d.zustand === "returning";
}

async function staubsaugerLaden() {
  try {
    staubsaugerZeigen(await api("staubsauger"));
  } catch { /* Anzeige bleibt, wie sie war */ }
}

$$("[data-saug]").forEach((knopf) => knopf.addEventListener("click", async () => {
  const befehl = knopf.dataset.saug;
  $$("[data-saug]").forEach((k) => (k.disabled = true));
  try {
    await ausfuehren(() => api("staubsauger/" + befehl, {}));
    if (navigator.vibrate) navigator.vibrate(80);
    melde(befehl === "start" ? "Der Staubsauger legt los." : "Der Staubsauger fährt zur Station.", "ok");
    $("#sg-status").textContent = befehl === "start" ? "Startet …" : "Fährt zur Station …";
  } catch { /* Meldung steht schon */ }
  // Der Sauger meldet den neuen Zustand mit etwas Verzoegerung
  setTimeout(staubsaugerLaden, 6000);
}));

/* ---------- Familie: Anzeigen aktuell halten ---------- */

function familieLaden() {
  if (!$("#warmwasser").hidden) warmwasserLaden();
  if (!$("#staubsauger").hidden) staubsaugerLaden();
}

setInterval(() => { if (!document.hidden) familieLaden(); }, 30000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) familieLaden(); });

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
laden();
