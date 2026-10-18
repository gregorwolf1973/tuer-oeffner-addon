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
    warn.push("Noch niemand gehört zur Familie – Warmwasser und Staubsauger sieht in der App daher niemand. In der Spalte „Familie“ auf „Hinzufügen“ drücken.");
  }
  if (!e.benachrichtigung) warn.push("Kein Benachrichtigungsdienst eingestellt – du erfährst nicht, wer öffnet.");
  $("#warnungen").replaceChildren(...warn.map((w) => el("p", w, "hinweis")));

  $("#benutzer").replaceChildren(...d.benutzer.map((b) => {
    const tr = el("tr");
    tr.append(el("td", b.name), el("td", b.benutzername || "–"), el("td", b.email));
    const st = el("td");
    st.append(el("span", STATUS[b.status] || b.status, "status " + b.status));
    tr.append(st);
    tr.append(el("td", b.fern ? "ja" : "nein"));
    const fam = el("td");
    if (familieAn && b.status !== "gesperrt") {
      fam.append(b.familie
        ? knopf("✓ Familie – entfernen", () => aktion(b, "familie_aus"), "an")
        : knopf("Hinzufügen", () => aktion(b, "familie_an")));
    } else {
      fam.textContent = b.familie ? "ja" : "–";
    }
    tr.append(fam, el("td", b.zwei_faktor ? "an" : "aus"),
      el("td", String(b.passkeys)), el("td", zeit(b.zuletzt_geoeffnet)));
    const td = el("td");
    if (b.status === "wartet") td.append(knopf("Freigeben", () => aktion(b, "freigeben")));
    if (b.status === "aktiv" || b.status === "wartet") {
      td.append(knopf(b.fern ? "Fern-Recht entziehen" : "Fern-Recht geben",
        () => aktion(b, b.fern ? "fern_aus" : "fern_an",
          b.fern ? null : `${b.name} darf dann von überall öffnen. Sicher?`)));
    }
    if (b.status === "gesperrt") td.append(knopf("Entsperren", () => aktion(b, "entsperren")));
    else td.append(knopf("Sperren", () => aktion(b, "sperren", `Zugang von ${b.name} sperren?`)));
    td.append(knopf("2FA zurücksetzen", () => aktion(b, "2fa_zuruecksetzen",
      `2FA und Passkeys von ${b.name} zurücksetzen? Danach muss ${b.name} die 2FA neu einrichten und du gibst wieder frei.`)));
    td.append(knopf("Löschen", () => aktion(b, "loeschen", `Zugang von ${b.name} endgültig löschen?`), "gefahr"));
    tr.append(td);
    return tr;
  }));
  if (!d.benutzer.length) {
    const tr = el("tr");
    const td = el("td", "Noch niemand registriert. Den Freunden die Adresse geben – sie registrieren sich selbst.", "leise");
    td.colSpan = 10;
    tr.append(td);
    $("#benutzer").replaceChildren(tr);
  }

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
      ["Warmwasser lädt", e.warmwasser_laeuft || "–"]);
  }
  zeilen.push(["Staubsauger", e.staubsauger_entitaet || "aus"]);
  if (e.staubsauger_entitaet) zeilen.push(["Staubsauger-Akku", e.staubsauger_batterie || "–"]);
  zeilen.push(["Kamera (Familie)", e.kamera_entitaet || "aus"]);
  $("#einstellungen").replaceChildren(...zeilen.flatMap(([k, v]) => [el("dt", k), el("dd", v)]));
}

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
setInterval(laden, 30000);
