/* Verwaltung - laeuft nur ueber den Ingress von Home Assistant. */
"use strict";

const $ = (sel) => document.querySelector(sel);

async function api(pfad, daten) {
  const opt = { headers: { "X-Tuer": "1" }, credentials: "same-origin" };
  if (daten !== undefined) {
    opt.method = "POST";
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(daten);
  }
  const antwort = await fetch("api/" + pfad, opt);
  const inhalt = await antwort.json().catch(() => ({}));
  if (!antwort.ok) throw new Error(inhalt.fehler || "Fehler " + antwort.status);
  return inhalt;
}

function melde(text, art = "fehler") {
  const m = $("#meldung");
  m.textContent = text;
  m.className = "meldung " + art;
  m.hidden = !text;
}

const zeit = (s) => (s ? new Date(s * 1000).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" }) : "–");

function el(tag, text, klasse) {
  const e = document.createElement(tag);
  if (text !== undefined && text !== null) e.textContent = text;
  if (klasse) e.className = klasse;
  return e;
}

const STATUS = { neu: "2FA fehlt", wartet: "wartet auf Freigabe", aktiv: "aktiv", gesperrt: "gesperrt" };
const AKTION_TEXT = {
  geoeffnet: "geöffnet", zu_weit: "zu weit weg", fehler: "Fehler beim Öffnen",
  registriert: "registriert", "2fa_eingerichtet": "2FA eingerichtet",
  "2fa_eingeschaltet": "2FA eingeschaltet", "2fa_ausgeschaltet": "2FA ausgeschaltet",
  passkey_angelegt: "Passkey angelegt", passkey_geloescht: "Passkey entfernt",
  passwort_geaendert: "Passwort geändert", konto_kurz_gesperrt: "15 min gesperrt (Fehlversuche)",
  admin_freigeben: "freigegeben", admin_sperren: "gesperrt", admin_entsperren: "entsperrt",
  admin_fern_an: "Fern-Recht erteilt", admin_fern_aus: "Fern-Recht entzogen",
  warmwasser: "Warmwasser aufgeheizt",
  staubsauger_start: "Staubsauger gestartet", staubsauger_zur_station: "Staubsauger zur Station",
  admin_familie_an: "zur Familie hinzugefügt", admin_familie_aus: "aus der Familie entfernt",
  admin_2fa_zuruecksetzen: "2FA zurückgesetzt", admin_loeschen: "gelöscht",
  admin_warmwasser_ziel: "Warmwasser-Ziel geändert",
  standort_an: "Standort-Freigabe an", standort_aus: "Standort-Freigabe aus",
  owntracks_eingerichtet: "OwnTracks eingerichtet", owntracks_getrennt: "OwnTracks getrennt",
};

function knopf(text, fn, klasse = "") {
  const k = el("button", text, "knopf klein " + klasse);
  k.type = "button";
  k.onclick = fn;
  return k;
}

async function aktion(b, was, frage) {
  if (frage && !confirm(frage)) return;
  try {
    await api(`admin/benutzer/${b.id}/${was}`, {});
    melde("");
  } catch (e) {
    melde(e.message);
  }
  laden();
}

/* Ein Recht als Schalter: ganze Zeile anklickbar, Zustand als aria-checked */
function recht(titel, hinweis, an, fn, moeglich = true) {
  const k = el("button", null, "recht");
  k.type = "button";
  k.setAttribute("role", "switch");
  k.setAttribute("aria-checked", String(Boolean(an)));
  k.disabled = !moeglich;
  const text = el("span", titel);
  text.append(el("small", hinweis));
  k.append(text, el("span", null, "schalter"));
  k.onclick = fn;
  return k;
}

function zugangKarte(b, familieAn) {
  const karte = el("article", null, "zugang " + b.status);
  const kopf = el("div", null, "zugang-kopf");
  kopf.append(el("span", b.name, "zugang-name"), el("span", STATUS[b.status] || b.status, "status " + b.status));
  karte.append(kopf, el("div", [b.benutzername ? "@" + b.benutzername : null, b.email].filter(Boolean).join(" · "), "zugang-wer"));

  const fakten = el("div", null, "zugang-fakten");
  const fakt = (name, wert) => {
    const f = el("span", name + " ");
    f.append(el("b", wert));
    return f;
  };
  fakten.append(fakt("2FA", b.zwei_faktor ? "an" : "aus"), fakt("Passkeys", String(b.passkeys)),
    fakt("Zuletzt geöffnet", zeit(b.zuletzt_geoeffnet)));
  karte.append(fakten);

  const offen = b.status === "aktiv" || b.status === "wartet";
  const rechte = el("div", null, "rechte");
  rechte.append(recht("Aus der Ferne öffnen", "ohne Standortprüfung", b.fern,
    () => aktion(b, b.fern ? "fern_aus" : "fern_an", b.fern ? null : `${b.name} darf dann von überall öffnen. Sicher?`),
    offen));
  if (familieAn) {
    rechte.append(recht("Familie", "Warmwasser, Staubsauger, Kamera, Pollen, Standorte", b.familie,
      () => aktion(b, b.familie ? "familie_aus" : "familie_an"), b.status !== "gesperrt"));
  }
  karte.append(rechte);

  const aktionen = el("div", null, "zugang-aktionen");
  if (b.status === "wartet") aktionen.append(knopf("Freigeben", () => aktion(b, "freigeben"), "haupt"));
  if (b.status === "gesperrt") aktionen.append(knopf("Entsperren", () => aktion(b, "entsperren")));
  if (b.status === "neu") aktionen.append(el("span", "richtet noch die 2FA ein", "leise"));
  const mehr = el("details", null, "mehr");
  mehr.append(el("summary", "Mehr ▾"));
  const liste = el("div", null, "mehr-liste");
  if (b.status !== "gesperrt") liste.append(knopf("Sperren", () => aktion(b, "sperren", `Zugang von ${b.name} sperren?`)));
  liste.append(knopf("2FA zurücksetzen", () => aktion(b, "2fa_zuruecksetzen",
    `2FA und Passkeys von ${b.name} zurücksetzen? Danach muss ${b.name} die 2FA neu einrichten und du gibst wieder frei.`)));
  liste.append(knopf("Löschen", () => aktion(b, "loeschen", `Zugang von ${b.name} endgültig löschen?`), "gefahr"));
  mehr.append(liste);
  aktionen.append(mehr);
  karte.append(aktionen);
  return karte;
}

/* ---------- Warmwasser-Zieltemperatur ---------- */

const ww = { daten: null, wert: null, geaendert: false };
const gradText = (w) => (w == null ? "–" : `${String(w).replace(".", ",")} °C`);

function warmwasserZeigen(d) {
  $("#ww-verwaltung").hidden = !d;
  if (!d) return;
  ww.daten = d;
  $("#ww-v-temperatur").textContent = gradText(d.temperatur);
  $("#ww-v-status").textContent = d.laeuft ? "Im Speicher · Aufheizen ist angefordert" : "Im Speicher";
  // Was gerade eingestellt wird, nicht beim Auffrischen ueberschreiben
  if (!ww.geaendert) ww.wert = d.ziel;
  zielAnzeigen();
}

function zielAnzeigen() {
  const d = ww.daten;
  $("#ww-ziel-anzeige").textContent = gradText(ww.wert);
  $('[data-schritt="-1"]').disabled = ww.wert == null || ww.wert - d.schritt < d.min;
  $('[data-schritt="1"]').disabled = ww.wert == null || ww.wert + d.schritt > d.max;
  $("#ww-ziel-speichern").disabled = !ww.geaendert;
  $("#ww-ziel-speichern").textContent = ww.geaendert ? `Auf ${gradText(ww.wert)} stellen` : "Übernehmen";
}

document.querySelectorAll("[data-schritt]").forEach((k) => k.addEventListener("click", () => {
  const d = ww.daten;
  ww.wert = Math.round((ww.wert + Number(k.dataset.schritt) * d.schritt) * 10) / 10;
  ww.geaendert = ww.wert !== d.ziel;
  zielAnzeigen();
}));

$("#ww-ziel-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("#ww-ziel-speichern").disabled = true;
  try {
    await api("admin/warmwasser/ziel", { wert: ww.wert });
    ww.geaendert = false;
    melde(`Warmwasser-Ziel ist jetzt ${gradText(ww.wert)}. Die Wärmepumpe übernimmt es in ein bis zwei Minuten.`, "ok");
  } catch (e) {
    melde(e.message);
  }
  laden();
});

async function laden() {
  let d;
  try {
    d = await api("admin/uebersicht");
  } catch (e) {
    return melde(e.message);
  }

  const e = d.einstellungen;
  const warn = [];
  if (!e.tuer_entitaet) warn.push("Es ist keine Tür-Entität eingestellt – Öffnen geht noch nicht.");
  else if (!e.entitaet_typ_ok) warn.push(`„${e.tuer_entitaet}“ ist kein unterstützter Typ (switch, button, script, lock, automation …).`);
  if (!e.heim_bekannt) warn.push("Der Standort der Haustür ist unbekannt – Öffnen in der Nähe geht nicht.");
  if (!e.externe_adresse.startsWith("https://")) warn.push("Keine externe https-Adresse eingestellt – Passkeys sind aus.");
  if (!e.warmwasser_typ_ok) warn.push(`„${e.warmwasser_entitaet}“ ist kein unterstützter Typ fürs Warmwasser (button, switch, script …).`);
  if (!e.staubsauger_typ_ok) warn.push(`„${e.staubsauger_entitaet}“ ist kein Staubsauger (vacuum.…).`);
  const familieAn = e.warmwasser_entitaet || e.staubsauger_entitaet || e.kamera_entitaet;
  if (familieAn && !d.benutzer.some((b) => b.familie)) {
    warn.push("Noch niemand gehört zur Familie – Warmwasser und Staubsauger sieht in der App daher niemand. Beim jeweiligen Zugang den Schalter „Familie“ einschalten.");
  }
  if (!e.benachrichtigung) warn.push("Kein Benachrichtigungsdienst eingestellt – du erfährst nicht, wer öffnet.");
  $("#warnungen").replaceChildren(...warn.map((w) => el("p", w, "hinweis")));

  $("#zaehler").textContent = d.benutzer.length
    ? `${d.benutzer.filter((b) => b.status === "aktiv").length} aktiv · ${d.benutzer.length} insgesamt`
    : "";
  $("#benutzer").replaceChildren(...d.benutzer.map((b) => zugangKarte(b, familieAn)));
  if (!d.benutzer.length) {
    $("#benutzer").replaceChildren(el("p",
      "Noch niemand registriert. Den Freunden die Adresse geben – sie registrieren sich selbst.", "leise"));
  }

  warmwasserZeigen(d.warmwasser);

  $("#protokoll").replaceChildren(...d.protokoll.map((p) => {
    const tr = el("tr");
    tr.append(el("td", zeit(p.zeit)), el("td", p.name || "–"), el("td", AKTION_TEXT[p.aktion] || p.aktion),
      el("td", p.detail || ""), el("td", p.ip || ""));
    return tr;
  }));

  const zeilen = [
    ["Tür-Entität", e.tuer_entitaet || "–"],
    ["Externe Adresse", e.externe_adresse || "–"],
    ["Radius", `${e.radius_meter} m`],
    ["Benachrichtigung", e.benachrichtigung ? "notify." + e.benachrichtigung : "–"],
    ["Standort der Tür", e.heim_bekannt ? "bekannt" : "unbekannt"],
    ["Registrierung", e.registrierung_offen ? "offen" : "geschlossen"],
    ["2FA", e.zwei_faktor_pflicht ? "Pflicht" : "freiwillig (jeder schaltet selbst)"],
    ["Warmwasser", e.warmwasser_entitaet || "aus"],
  ];
  if (e.warmwasser_entitaet) {
    zeilen.push(["Warmwasser-Temperatur", e.warmwasser_temperatur || "–"],
      ["Warmwasser lädt", e.warmwasser_laeuft || "–"],
      ["Warmwasser-Ziel", e.warmwasser_soll || "–"],
      ["Wärmepumpe heizt", e.warmwasser_heizt || "–"]);
  }
  zeilen.push(["Staubsauger", e.staubsauger_entitaet || "aus"]);
  if (e.staubsauger_entitaet) zeilen.push(["Staubsauger-Akku", e.staubsauger_batterie || "–"]);
  zeilen.push(["Kamera (Familie)", e.kamera_entitaet || "aus"],
    ["Pollenflug (Familie)", e.pollen_praefix ? e.pollen_praefix + "…" : "aus"], ["Gäste-WLAN", e.wlan_name || "aus"]);
  $("#einstellungen").replaceChildren(...zeilen.flatMap(([k, v]) => [el("dt", k), el("dd", v)]));
}

// Ein offenes "Mehr"-Menue schliesst bei einem Klick daneben
document.addEventListener("click", (ev) => {
  document.querySelectorAll(".mehr[open]").forEach((m) => { if (!m.contains(ev.target)) m.open = false; });
});

$("#test-oeffnen").onclick = async () => {
  if (!confirm("Haustür jetzt öffnen?")) return;
  try {
    await api("admin/oeffnen", {});
    melde("Tür geöffnet.", "ok");
  } catch (e) {
    melde(e.message);
  }
  laden();
};

laden();
setInterval(() => { if (!document.querySelector(".mehr[open]")) laden(); }, 30000);
