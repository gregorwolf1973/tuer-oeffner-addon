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
  $("#kamera").hidden = !zustand.kamera;
  $("#wlan").hidden = !zustand.wlan;
  $("#pollen").hidden = !zustand.pollen;
  $("#standorte").hidden = !zustand.standorte;
  ort.teilen = Boolean(zustand.standort_teilen);
  ortKnopfZeigen();
  owntracksZeigen();
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
  kameraPruefen();
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
    // Schluessel nicht fuer den naechsten auf diesem Geraet stehen lassen
    $("#wlan-daten").hidden = true;
    $("#wlan-qr").replaceChildren();
    $("#wlan-schluessel").textContent = "";
    $("#wlan-zeigen").textContent = "WLAN-QR-Code zeigen";
    // Auch die OwnTracks-Zugangsdaten
    $("#ot-anleitung").hidden = true;
    $("#ot-link").href = "#";
    $("#ot-passwort").textContent = "";
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
  // Ueber dem Sollwert bewirkt eine Ladung nichts - das sagen wir ehrlich
  const warmGenug = d.temperatur != null && d.soll != null && d.temperatur >= d.soll;
  const ziel = d.soll == null ? "" : ` · Ziel ${String(d.soll).replace(".", ",")} °C`;
  knopf.disabled = wwStartet || d.laeuft === true || warmGenug;
  let knopfText = "Wasser aufheizen";
  let status = "Temperatur im Speicher" + ziel;
  if (d.laeuft && d.heizt) {
    knopfText = "Heizt gerade auf …";
    status = "Die Wärmepumpe heizt gerade das Wasser auf" + ziel + ".";
  } else if (d.laeuft) {
    knopfText = "Aufheizen angefordert";
    status = warmGenug
      ? "Angefordert – das Wasser ist aber schon warm genug, die Wärmepumpe bleibt aus."
      : "Angefordert – die Wärmepumpe startet, sobald sie kann.";
  } else if (warmGenug) {
    knopfText = "Schon warm genug";
    status = "Das Wasser ist warm genug" + ziel + ".";
  }
  knopf.textContent = knopfText;
  $("#ww-status").textContent = status;
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
    melde("Aufheizen angefordert.", "ok");
    knopf.textContent = "Aufheizen angefordert";
    $("#ww-status").textContent = "Angefordert. Die Wärmepumpe meldet sich gleich.";
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

/* ---------- Kamera ----------
   Ein Bild nach dem anderen, etwa jede Sekunde - aber nur, solange die App
   im Vordergrund ist UND das Bild auf dem Bildschirm zu sehen ist. Sonst
   geht keine einzige Anfrage raus. */

const kamera = { laeuft: false, imBild: false, uhr: null };
const kameraBild = $("#kamera-bild");

function kameraSoll() {
  return zustand.kamera && !document.hidden && kamera.imBild
    && !$("#kamera").hidden && !$("#oeffnen").hidden && !$('[data-ansicht="haupt"]').hidden;
}

function kameraNaechstes() {
  clearTimeout(kamera.uhr);
  if (!kameraSoll()) {
    kamera.laeuft = false;
    $("#kamera-live").hidden = true;
    return;
  }
  kamera.laeuft = true;
  kameraBild.src = "api/kamera?t=" + Date.now();
}

function kameraPruefen() {
  if (kameraSoll() && !kamera.laeuft) kameraNaechstes();
}

kameraBild.addEventListener("load", () => {
  $("#kamera-text").textContent = "";
  $("#kamera-live").hidden = false;
  kamera.uhr = setTimeout(kameraNaechstes, 1000);
});
kameraBild.addEventListener("error", () => {
  if (!kameraBild.getAttribute("src")) return;
  $("#kamera-text").textContent = "Die Kamera liefert gerade kein Bild.";
  $("#kamera-live").hidden = true;
  // Seltener nachfragen, solange die Kamera nicht antwortet
  kamera.uhr = setTimeout(kameraNaechstes, 5000);
});

if ("IntersectionObserver" in window) {
  new IntersectionObserver(([eintrag]) => {
    kamera.imBild = eintrag.isIntersecting;
    kameraPruefen();
  }).observe($("#kamera"));
} else {
  kamera.imBild = true;
}
document.addEventListener("visibilitychange", kameraPruefen);

/* ---------- Gaeste-WLAN ---------- */

$("#wlan-zeigen").addEventListener("click", async () => {
  const knopf = $("#wlan-zeigen");
  const daten = $("#wlan-daten");
  if (!daten.hidden) {
    daten.hidden = true;
    knopf.textContent = "WLAN-QR-Code zeigen";
    knopf.setAttribute("aria-expanded", "false");
    return;
  }
  try {
    const d = await ausfuehren(() => api("wlan"));
    // Das SVG kommt von unserem eigenen Server (segno), kein Fremdinhalt
    $("#wlan-qr").innerHTML = d.svg;
    $("#wlan-name").textContent = d.name;
    $("#wlan-schluessel").textContent = d.schluessel || "– (offen)";
    $("#wlan-kopieren").hidden = !d.schluessel || !navigator.clipboard;
    daten.hidden = false;
    knopf.textContent = "QR-Code ausblenden";
    knopf.setAttribute("aria-expanded", "true");
  } catch { /* Meldung steht schon */ }
});

$("#wlan-kopieren").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("#wlan-schluessel").textContent);
    melde("Schlüssel kopiert.", "ok");
  } catch {
    melde("Kopieren ging nicht – bitte von Hand abschreiben.");
  }
});

/* ---------- Pollenflug (DWD-Index 0 bis 3 in halben Stufen) ---------- */

const POLLEN_STUFE = {
  0: "keine", 0.5: "keine bis gering", 1: "gering", 1.5: "gering bis mittel",
  2: "mittel", 2.5: "mittel bis hoch", 3: "hoch",
};
const pollen = { zuletzt: 0 };

function pollenZelle(wert) {
  const td = document.createElement("td");
  const s = document.createElement("span");
  // Klasse nach ganzer Stufe: 0 keine, 1 gering, 2 mittel, 3 hoch
  s.className = "pollen-stufe s" + (wert == null ? "x" : Math.ceil(wert));
  s.textContent = wert == null ? "–" : POLLEN_STUFE[wert] || String(wert);
  td.append(s);
  return td;
}

function pollenZeigen(d) {
  const tage = (p) => [p.heute, p.morgen, p.uebermorgen];
  const belastet = d.arten.filter((p) => tage(p).some((v) => v > 0));
  const ruhig = d.arten.filter((p) => !belastet.includes(p));
  const stand = d.stand ? new Date(d.stand) : null;
  $("#pollen-stand").textContent = stand && !isNaN(stand)
    ? "DWD, " + stand.toLocaleDateString("de-DE", { weekday: "short", day: "numeric", month: "numeric" })
    : "";

  if (!d.arten.length) {
    $("#pollen-fazit").textContent = "Keine Pollendaten gefunden.";
  } else if (!belastet.length) {
    $("#pollen-fazit").textContent = "Heute bis übermorgen keine Belastung. 🌿";
  } else {
    const heute = belastet.filter((p) => p.heute > 0).map((p) => p.name);
    $("#pollen-fazit").textContent = heute.length
      ? `Heute unterwegs: ${heute.join(", ")}.`
      : "Heute keine Belastung – in den nächsten Tagen schon:";
  }

  const tabelle = $("#pollen-belastet");
  tabelle.hidden = !belastet.length;
  $("tbody", tabelle).replaceChildren(...belastet.map((p) => {
    const tr = document.createElement("tr");
    const th = document.createElement("th");
    th.scope = "row";
    th.textContent = p.name;
    tr.append(th, ...tage(p).map(pollenZelle));
    return tr;
  }));

  const weitere = $("#pollen-weitere");
  weitere.hidden = !belastet.length || !ruhig.length;
  $("summary", weitere).textContent = `${ruhig.length} weitere ohne Belastung`;
  $("#pollen-weitere-liste").textContent = ruhig.map((p) => p.name).join(", ");
}

async function pollenLaden() {
  // Der DWD rechnet einmal am Tag - alle 10 Minuten nachsehen reicht
  if (Date.now() - pollen.zuletzt < 10 * 60 * 1000) return;
  try {
    pollenZeigen(await api("pollen"));
    pollen.zuletzt = Date.now();
  } catch { /* Anzeige bleibt, wie sie war */ }
}

/* ---------- Wo ist die Familie? ----------
   Jeder schaltet die Freigabe selbst ein. Gesendet wird alle 2 Minuten,
   aber nur solange die App offen und im Vordergrund ist. */

const ORT_SENDEN_MS = 2 * 60 * 1000;
const ORT_LADEN_MS = 60 * 1000;
const ort = { teilen: false, gesendet: 0, geladen: 0, daten: null, zoom: 0 };

function ortKnopfZeigen() {
  $("#standort-knopf").textContent = ort.teilen ? "Freigabe beenden" : "Meinen Standort freigeben";
  $("#standort-knopf").classList.toggle("aus", ort.teilen);
  $("#standort-hinweis").textContent = ort.teilen
    ? "Dein Standort wird geteilt, solange diese App offen ist."
    : "Die Familie sieht dann, wo du bist. Gesendet wird nur, solange diese App offen ist.";
}

async function ortSenden(sofort = false) {
  if (!ort.teilen || document.hidden || $("#standorte").hidden) return;
  if (!sofort && Date.now() - ort.gesendet < ORT_SENDEN_MS) return;
  ort.gesendet = Date.now();
  try {
    await api("standort", await standort());
    ortLaden(true);
  } catch { /* naechster Versuch in 2 Minuten */ }
}

async function ortLaden(sofort = false) {
  if (!sofort && Date.now() - ort.geladen < ORT_LADEN_MS) return;
  try {
    ort.daten = await api("standorte");
    ort.geladen = Date.now();
    ortZeigen();
  } catch { /* Anzeige bleibt, wie sie war */ }
}

function alterText(sek) {
  if (sek < 90) return "gerade eben";
  if (sek < 3600) return `vor ${Math.round(sek / 60)} Min.`;
  if (sek < 86400) return `vor ${Math.round(sek / 3600)} Std.`;
  const tage = Math.round(sek / 86400);
  return tage === 1 ? "vor 1 Tag" : `vor ${tage} Tagen`;
}

function wegText(m) {
  if (m == null) return "";
  if (m < 150) return "zu Hause";
  if (m < 1000) return `${Math.round(m / 10) * 10} m von zu Hause`;
  return `${(m / 1000).toFixed(m < 10000 ? 1 : 0).replace(".", ",")} km von zu Hause`;
}

function ortZeigen() {
  const d = ort.daten;
  if (!d) return;
  $("#standort-leer").hidden = d.leute.length > 0;
  $("#standort-liste").replaceChildren(...d.leute.map((p) => {
    const li = document.createElement("li");
    const alt = d.jetzt - p.zeit;
    if (alt > 3600) li.className = "alt";
    const kopf = document.createElement("div");
    const name = document.createElement("b");
    name.textContent = p.ich ? `${p.name} (du)` : p.name;
    const wo = document.createElement("span");
    wo.textContent = [wegText(p.von_zuhause_m), alterText(alt)].filter(Boolean).join(" · ");
    kopf.append(name, wo);
    const link = document.createElement("a");
    link.href = `https://www.google.com/maps/search/?api=1&query=${p.lat},${p.lon}`;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = "In Karten öffnen";
    li.append(kopf, link);
    return li;
  }));
  karteZeichnen();
}

/* Eine kleine Karte aus OpenStreetMap-Kacheln, ohne Bibliothek */
const KACHEL = 256;
const merc = (lat, lon, z) => {
  const n = KACHEL * 2 ** z;
  const r = (lat * Math.PI) / 180;
  return { x: ((lon + 180) / 360) * n, y: ((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * n };
};

function karteZeichnen() {
  const d = ort.daten;
  const box = $("#osm");
  box.hidden = !d || !d.leute.length;
  if (box.hidden) return;
  const punkte = d.leute.map((p) => ({ lat: p.lat, lon: p.lon, text: p.name.slice(0, 1).toUpperCase(), titel: p.name, alt: d.jetzt - p.zeit > 3600 }));
  if (d.heim) punkte.push({ lat: d.heim.lat, lon: d.heim.lon, text: "🏠", titel: "Zu Hause", heim: true });
  const w = box.clientWidth;
  const h = box.clientHeight;
  // Groesste Naehe, bei der alle Punkte auf die Karte passen
  let z = 17;
  for (; z > 3; z--) {
    const p = punkte.map((q) => merc(q.lat, q.lon, z));
    const breit = Math.max(...p.map((q) => q.x)) - Math.min(...p.map((q) => q.x));
    const hoch = Math.max(...p.map((q) => q.y)) - Math.min(...p.map((q) => q.y));
    if (breit <= w - 70 && hoch <= h - 70) break;
  }
  z = Math.max(3, Math.min(18, z + ort.zoom));
  const px = punkte.map((q) => merc(q.lat, q.lon, z));
  const mx = (Math.max(...px.map((q) => q.x)) + Math.min(...px.map((q) => q.x))) / 2;
  const my = (Math.max(...px.map((q) => q.y)) + Math.min(...px.map((q) => q.y))) / 2;
  const links = mx - w / 2;
  const oben = my - h / 2;
  const anzahl = 2 ** z;
  const kacheln = [];
  for (let tx = Math.floor(links / KACHEL); tx <= Math.floor((links + w) / KACHEL); tx++) {
    for (let ty = Math.floor(oben / KACHEL); ty <= Math.floor((oben + h) / KACHEL); ty++) {
      if (ty < 0 || ty >= anzahl) continue;
      const img = document.createElement("img");
      img.alt = "";
      img.referrerPolicy = "origin";  // OpenStreetMap verlangt eine Herkunft
      img.src = `https://tile.openstreetmap.org/${z}/${((tx % anzahl) + anzahl) % anzahl}/${ty}.png`;
      img.style.left = `${tx * KACHEL - links}px`;
      img.style.top = `${ty * KACHEL - oben}px`;
      kacheln.push(img);
    }
  }
  $(".osm-kacheln", box).replaceChildren(...kacheln);
  $(".osm-punkte", box).replaceChildren(...punkte.map((q, i) => {
    const m = document.createElement("span");
    m.className = "osm-punkt" + (q.heim ? " heim" : "") + (q.alt ? " alt" : "");
    m.textContent = q.text;
    m.title = q.titel;
    m.style.left = `${px[i].x - links}px`;
    m.style.top = `${px[i].y - oben}px`;
    return m;
  }));
}

$$("[data-zoom]").forEach((k) => k.addEventListener("click", () => {
  ort.zoom = Math.max(-8, Math.min(6, ort.zoom + Number(k.dataset.zoom)));
  karteZeichnen();
}));

$("#standort-knopf").addEventListener("click", () => ausfuehren(async () => {
  const knopf = $("#standort-knopf");
  knopf.disabled = true;
  try {
    if (ort.teilen) {
      await api("standort/teilen", { an: false });
      ort.teilen = false;
      melde("Dein Standort wird nicht mehr geteilt.", "ok");
    } else {
      // Erst den Standort holen - so fragt das Handy jetzt nach der Erlaubnis
      const pos = await standort();
      await api("standort/teilen", { an: true });
      ort.teilen = true;
      await api("standort", pos);
      ort.gesendet = Date.now();
      melde("Dein Standort ist für die Familie sichtbar.", "ok");
    }
    ortKnopfZeigen();
    await ortLaden(true);
  } finally {
    knopf.disabled = false;
  }
}).catch(() => {}));

/* ---------- OwnTracks: Standort auch im Hintergrund ---------- */

function owntracksZeigen() {
  $("#ot").hidden = !zustand.standorte || !zustand.owntracks_moeglich;
  let text;
  if (!zustand.owntracks) {
    text = "Mit der kostenlosen App OwnTracks wird dein Standort auch im Hintergrund geschickt – direkt zu uns nach Hause, nicht an Google.";
  } else if (zustand.owntracks_zuletzt) {
    text = `✓ OwnTracks ist verbunden – letzte Meldung ${alterText(Date.now() / 1000 - zustand.owntracks_zuletzt)}.`;
  } else {
    text = "Eingerichtet – wartet auf die erste Meldung von OwnTracks.";
  }
  if (zustand.owntracks && !zustand.standort_teilen) text += " Die Freigabe ist aus, deshalb sieht gerade niemand deinen Standort.";
  $("#ot-status").textContent = text;
  $("#ot-einrichten").textContent = zustand.owntracks ? "Neu einrichten (z. B. neues Handy)" : "OwnTracks einrichten";
  $("#ot-trennen").hidden = !zustand.owntracks;
}

$("#ot-einrichten").addEventListener("click", () => ausfuehren(async () => {
  if (zustand.owntracks && !confirm("Neu einrichten? Das bisher eingerichtete Handy meldet sich danach nicht mehr.")) return;
  const d = await api("owntracks/einrichten", {});
  $("#ot-link").href = d.link;
  $("#ot-url").textContent = d.url;
  $("#ot-benutzer").textContent = d.benutzer;
  $("#ot-passwort").textContent = d.passwort;
  $("#ot-anleitung").hidden = false;
  await laden();
  $("#ot-anleitung").scrollIntoView({ block: "nearest" });
}).catch(() => {}));

$("#ot-trennen").addEventListener("click", () => ausfuehren(async () => {
  if (!confirm("OwnTracks trennen? Dein Standort kommt dann nur noch, solange diese App offen ist.")) return;
  await api("owntracks/trennen", {});
  $("#ot-anleitung").hidden = true;
  melde("OwnTracks ist getrennt.", "ok");
  await laden();
}).catch(() => {}));

/* ---------- Familie: Anzeigen aktuell halten ---------- */

function familieLaden() {
  if (!$("#warmwasser").hidden) warmwasserLaden();
  if (!$("#staubsauger").hidden) staubsaugerLaden();
  if (!$("#pollen").hidden) pollenLaden();
  if (!$("#standorte").hidden) {
    ortSenden();
    ortLaden();
  }
}

setInterval(() => { if (!document.hidden) familieLaden(); }, 30000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) familieLaden(); });

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
laden();
