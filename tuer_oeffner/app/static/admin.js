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
  admin_warmwasser_vorgabe: "Warmwasser-Vorgabe geändert",
  standort_an: "Standort-Freigabe an", standort_aus: "Standort-Freigabe aus",
  owntracks_eingerichtet: "OwnTracks eingerichtet", owntracks_getrennt: "OwnTracks getrennt",
  hd_antrag: "Hausdienst-Antrag", admin_hd_genehmigen: "Hausdienst-Antrag genehmigt",
  admin_hd_ablehnen: "Hausdienst-Antrag abgelehnt",
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

/* ---------- Warmwasser-Vorgabe fuers Aufheizen ---------- */

const ww = { daten: null, wert: null, geaendert: false };
const gradText = (w) => (w == null ? "–" : `${String(w).replace(".", ",")} °C`);

function warmwasserZeigen(d) {
  $("#ww-verwaltung").hidden = !d;
  if (!d) return;
  ww.daten = d;
  $("#ww-v-temperatur").textContent = gradText(d.temperatur);
  const eingestellt = d.eingestellt == null ? "" : ` · Wärmepumpe steht auf ${gradText(d.eingestellt)}`;
  $("#ww-v-status").textContent = "Im Speicher" + eingestellt + (d.laeuft ? " · Aufheizen ist angefordert" : "");
  // Was gerade eingestellt wird, nicht beim Auffrischen ueberschreiben
  if (!ww.geaendert) ww.wert = d.vorgabe;
  zielAnzeigen();
}

function zielAnzeigen() {
  const d = ww.daten;
  $("#ww-ziel-anzeige").textContent = gradText(ww.wert);
  $('[data-schritt="-1"]').disabled = ww.wert == null || ww.wert - d.schritt < d.min;
  $('[data-schritt="1"]').disabled = ww.wert == null || ww.wert + d.schritt > d.max;
  $("#ww-ziel-speichern").disabled = !ww.geaendert;
  $("#ww-ziel-speichern").textContent = ww.geaendert ? `${gradText(ww.wert)} vorgeben` : "Übernehmen";
}

document.querySelectorAll("[data-schritt]").forEach((k) => k.addEventListener("click", () => {
  const d = ww.daten;
  ww.wert = Math.round((ww.wert + Number(k.dataset.schritt) * d.schritt) * 10) / 10;
  ww.geaendert = ww.wert !== d.vorgabe;
  zielAnzeigen();
}));

$("#ww-ziel-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("#ww-ziel-speichern").disabled = true;
  try {
    await api("admin/warmwasser/vorgabe", { wert: ww.wert });
    ww.geaendert = false;
    melde(`Vorgabe ist jetzt ${gradText(ww.wert)}. Die Wärmepumpe wird beim nächsten Aufheizen darauf gestellt.`, "ok");
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

/* ---------- Hausdienst planen ----------
   Wird nicht alle 30 s neu gezeichnet, sondern nur nach einer Aenderung -
   sonst klappt eine Auswahl unter den Fingern wieder zu. */

const HD_STATUS = { offen: "offen", genehmigt: "genehmigt", abgelehnt: "abgelehnt", zurueckgezogen: "zurückgezogen" };
const hd = { personen: [], aufgaben: [] };

function liste(name, eintraege, leer) {
  const s = el("select");
  s.name = name;
  if (leer !== undefined) s.append(new Option(leer, ""));
  eintraege.forEach(([wert, text]) => s.append(new Option(text, wert)));
  return s;
}

const personenListe = () => hd.personen.map((p) => [p.id, p.name]);
const aufgabenListe = () => hd.aufgaben.map((a) => [a.id, a.name]);

async function hdSenden(pfad, daten, frage) {
  if (frage && !confirm(frage)) return;
  let ok = false;
  try {
    await api("admin/hausdienst/" + pfad, daten);
    melde("");
    ok = true;
  } catch (e) {
    melde(e.message);
  }
  hausdienstLaden();
  return ok;
}

/* Kleines Formular "Wer? Was? +" fuer einen weiteren Dienst */
function dienstDazu(beschriftung, senden) {
  const f = el("form", null, "hd-neu");
  const wer = liste("benutzer_id", personenListe(), "Wer?");
  const was = liste("aufgabe_id", aufgabenListe(), "Was?");
  wer.required = was.required = true;
  wer.setAttribute("aria-label", "Wer");
  was.setAttribute("aria-label", "Welche Aufgabe");
  const plus = el("button", "+", "knopf klein");
  plus.setAttribute("aria-label", beschriftung);
  f.append(wer, was, plus);
  f.onsubmit = (ev) => {
    ev.preventDefault();
    senden(Number(wer.value), Number(was.value));
  };
  return f;
}

function loeschKnopf(beschriftung, fn) {
  const k = knopf("×", fn, "gefahr");
  k.setAttribute("aria-label", beschriftung);
  return k;
}

function wocheZeigen(d) {
  $("#hd-woche").replaceChildren(...d.wochentage.map((name, tag) => {
    const spalte = el("div", null, "hd-tag");
    spalte.append(el("h4", name));
    d.plan.filter((p) => p.wochentag === tag).forEach((p) => {
      const z = el("div", null, "hd-dienst");
      z.append(el("span", `${p.name}: ${p.aufgabe}`),
        loeschKnopf(`${p.name}: ${p.aufgabe} am ${name} entfernen`, () => hdSenden(`plan/${p.id}/loeschen`, {})));
      spalte.append(z);
    });
    spalte.append(dienstDazu(`Dienst am ${name} hinzufügen`,
      (benutzer_id, aufgabe_id) => hdSenden("plan", { wochentag: tag, benutzer_id, aufgabe_id })));
    return spalte;
  }));
}

function tageZeigen(d) {
  $("#hd-tage").replaceChildren(...d.tage.map((t) => {
    const karte = el("div", null, "hd-tag");
    karte.append(el("h4", t.tag));
    if (!t.dienste.length) karte.append(el("p", "frei", "leise"));
    t.dienste.forEach((x) => {
      const z = el("div", null, "hd-dienst" + (x.abweichung_id ? " geaendert" : "") + (x.pausiert ? " aus" : ""));
      z.append(el("span", x.aufgabe + (x.pausiert ? " (Pause)" : "")));
      if (x.plan_id) {
        // Person wechseln oder ausfallen lassen
        const leute = personenListe();
        if (x.benutzer_id && !leute.some(([id]) => id === x.benutzer_id)) leute.push([x.benutzer_id, x.name]);
        const wer = liste("wer", leute, "– entfällt –");
        wer.value = x.benutzer_id ?? "";
        wer.setAttribute("aria-label", `Wer macht ${x.aufgabe} am ${t.tag}`);
        wer.onchange = () => hdSenden("tag", { datum: t.datum, plan_id: x.plan_id, benutzer_id: wer.value ? Number(wer.value) : null });
        z.append(wer);
        if (x.abweichung_id) z.append(knopf("wie im Plan", () => hdSenden(`tag/${x.abweichung_id}/loeschen`, {})));
      } else {
        z.append(el("span", `${x.name} (nur an diesem Tag)`, "leise"),
          loeschKnopf(`${x.aufgabe} am ${t.tag} entfernen`, () => hdSenden(`tag/${x.abweichung_id}/loeschen`, {})));
      }
      karte.append(z);
    });
    karte.append(dienstDazu(`Dienst nur am ${t.tag} hinzufügen`,
      (benutzer_id, aufgabe_id) => hdSenden("tag", { datum: t.datum, benutzer_id, aufgabe_id })));
    return karte;
  }));
}

function antraegeZeigen(d) {
  const offen = d.antraege.filter((a) => a.status === "offen").length;
  $("#hd-zaehler").textContent = offen ? `${offen} offene${offen === 1 ? "r" : ""} Antr${offen === 1 ? "ag" : "äge"}` : "";
  $("#hd-antraege").replaceChildren(...d.antraege.slice(0, 10).map((a) => {
    const z = el("div", null, "hd-antrag " + a.status);
    z.append(el("span", a.text), el("small", zeit(a.angelegt), "leise"),
      el("span", HD_STATUS[a.status] || a.status, "status " + (a.status === "genehmigt" ? "aktiv" : a.status === "offen" ? "wartet" : "neu")));
    if (a.status === "offen") {
      z.append(knopf("Genehmigen", () => hdSenden(`antrag/${a.id}/genehmigen`, {}), "haupt"),
        knopf("Ablehnen", () => hdSenden(`antrag/${a.id}/ablehnen`, {})));
    }
    return z;
  }));
}

function aufgabenZeigen(d) {
  $("#hd-aufgaben").replaceChildren(...d.aufgaben.map((a) => {
    const f = el("form", null, "hd-zeile");
    const name = el("input");
    const ansage = el("input");
    Object.assign(name, { name: "name", value: a.name, maxLength: 40, required: true });
    Object.assign(ansage, { name: "ansage", value: a.ansage, maxLength: 60, required: true });
    name.setAttribute("aria-label", "Name");
    ansage.setAttribute("aria-label", "In der Ansage");
    f.append(name, ansage, el("button", "Speichern", "knopf klein"),
      loeschKnopf(`${a.name} löschen`, () => hdSenden(`aufgabe/${a.id}/loeschen`, {},
        `Aufgabe „${a.name}“ löschen? Sie verschwindet auch aus dem Wochenplan.`)));
    f.onsubmit = (ev) => {
      ev.preventDefault();
      hdSenden("aufgabe", { id: a.id, name: name.value, ansage: ansage.value });
    };
    return f;
  }));
  if (!d.aufgaben.length) $("#hd-aufgaben").append(el("p", "Noch keine Aufgaben.", "leise"));
}

function pausenZeigen(d) {
  $("#hd-pausen").replaceChildren(...d.pausen.map((p) => {
    const z = el("div", null, "hd-zeile");
    z.append(el("span", `${p.name}: ${p.text}`),
      loeschKnopf(`Pause von ${p.name} entfernen`, () => hdSenden(`pause/${p.id}/loeschen`, {})));
    return z;
  }));
  if (!d.pausen.length) $("#hd-pausen").append(el("p", "Gerade keine Pausen.", "leise"));
  const wer = $('#hd-pause-neu [name="benutzer_id"]');
  wer.replaceChildren(new Option("Wer?", ""), ...personenListe().map(([id, n]) => new Option(n, id)));
}

/* Anrufe: Schalter, Ansage, Nummer und Uhrzeit je Person, Protokoll */
const KURZ_TAGE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];

function eingabe(attrs, beschriftung) {
  const i = el("input");
  Object.assign(i, attrs);
  if (beschriftung) i.setAttribute("aria-label", beschriftung);
  return i;
}

function anrufeZeigen(d) {
  const a = d.anrufe;
  const teile = [];
  if (!a.moeglich) teile.push(el("p", "In den Optionen des Add-ons ist ha-sip (Add-on, SIP-Server) nicht eingestellt – Anrufe gehen erst danach.", "hinweis"));
  teile.push(recht("Anrufe einschalten", `ha-sip ${a.sip_addon || "–"}, Konto ${a.sip_konto}`, a.an,
    () => hdSenden("anrufe", { an: !a.an }), a.moeglich));

  const ansage = el("form", null, "hd-ansage");
  const text = el("textarea");
  Object.assign(text, { value: a.ansage, rows: 2, maxLength: 400 });
  text.setAttribute("aria-label", "Ansage");
  ansage.append(el("label", "Ansage – Platzhalter {name}, {dienste}, {tag}"), text);
  const knoepfe = el("div", null, "hd-zeile");
  knoepfe.append(el("button", "Ansage speichern", "knopf klein"),
    knopf("Standard", () => { text.value = a.standard; }));
  ansage.append(knoepfe);
  ansage.onsubmit = (ev) => {
    ev.preventDefault();
    hdSenden("anrufe", { ansage: text.value });
  };
  teile.push(ansage);

  const tabelle = el("table", null, "hd-telefon");
  const kopf = el("tr");
  ["Person", "Nummer", "Uhrzeit", ...KURZ_TAGE, ""].forEach((t) => kopf.append(el("th", t)));
  const thead = el("thead");
  thead.append(kopf);
  const tbody = el("tbody");
  d.personen.forEach((p) => {
    const t = d.telefon[p.id] || { nummer: "", zeit: "", tage: {} };
    const tr = el("tr");
    const nummer = eingabe({ value: t.nummer, inputMode: "tel", placeholder: "0170…" }, `Nummer von ${p.name}`);
    const uhr = eingabe({ type: "time", value: t.zeit }, `Uhrzeit für ${p.name}`);
    const tage = KURZ_TAGE.map((k, i) => eingabe({ type: "time", value: t.tage[i] || "" }, `${p.name} am ${d.wochentage[i]} abweichend`));
    const speichern = knopf("Speichern", () => hdSenden("telefon", {
      benutzer_id: p.id, nummer: nummer.value, zeit: uhr.value,
      tage: Object.fromEntries(tage.map((x, i) => [i, x.value]).filter(([, v]) => v)),
    }));
    const test = knopf("Testanruf", async () => {
      if (!confirm(`${p.name} jetzt anrufen?`)) return;
      try {
        const r = await api("admin/hausdienst/testanruf", { benutzer_id: p.id });
        melde(`Ruft ${p.name} an: „${r.ansage}“`, "ok");
      } catch (e) {
        melde(e.message);
      }
      hausdienstLaden();
    });
    test.disabled = !a.moeglich || !t.nummer;
    const aktionen = el("td");
    aktionen.append(speichern, test);
    tr.append(el("td", p.name), ...[nummer, uhr, ...tage].map((x) => {
      const td = el("td");
      td.append(x);
      return td;
    }), aktionen);
    tbody.append(tr);
  });
  tabelle.append(thead, tbody);
  const rahmen = el("div", null, "tabelle-rahmen");
  rahmen.append(tabelle);
  teile.push(rahmen, el("p", "Leer gelassene Wochentage nutzen die Uhrzeit links. Ohne Uhrzeit oder Nummer wird nicht angerufen.", "leise"));

  if (d.anrufliste.length) {
    const liste = el("table");
    const k = el("tr");
    ["Wann", "Wer", "Anruf", "Ergebnis"].forEach((t) => k.append(el("th", t)));
    const lk = el("thead");
    lk.append(k);
    const lb = el("tbody");
    d.anrufliste.forEach((r) => {
      const tr = el("tr");
      const ergebnis = r.fehler ? "Fehler: " + r.fehler
        : r.bestaetigt ? "Taste 1 um " + new Date(r.bestaetigt * 1000).toLocaleTimeString("de-DE", { timeStyle: "short" })
          : "keine Taste 1 (Mailbox oder nicht abgenommen)";
      tr.append(el("td", zeit(r.zeit)), el("td", r.name), el("td", ["Test", "1.", "2."][r.versuch] || r.versuch),
        el("td", ergebnis));
      lb.append(tr);
    });
    liste.append(lk, lb);
    const r2 = el("div", null, "tabelle-rahmen");
    r2.append(liste);
    teile.push(el("h4", "Letzte Anrufe"), r2);
  }
  $("#hd-anrufe").replaceChildren(...teile);
}

async function hausdienstLaden() {
  let d;
  try {
    d = await api("admin/hausdienst");
  } catch (e) {
    return melde(e.message);
  }
  hd.personen = d.personen;
  hd.aufgaben = d.aufgaben;
  const hinweise = [];
  if (!d.personen.length) hinweise.push("Noch niemand gehört zur Familie – planen lässt sich nur, wer unten bei den Zugängen „Familie“ hat.");
  if (!d.aufgaben.length) hinweise.push("Zuerst unten die Aufgaben anlegen (z. B. Küche → Küchendienst), dann den Wochenplan füllen.");
  $("#hd-hinweise").replaceChildren(...hinweise.map((w) => el("p", w, "hinweis")));
  antraegeZeigen(d);
  wocheZeigen(d);
  tageZeigen(d);
  aufgabenZeigen(d);
  pausenZeigen(d);
  anrufeZeigen(d);
}

$("#hd-aufgabe-neu").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target.elements;
  if (await hdSenden("aufgabe", { name: f.namedItem("name").value, ansage: f.namedItem("ansage").value })) ev.target.reset();
});

$("#hd-pause-neu").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target.elements;
  const daten = { benutzer_id: Number(f.namedItem("benutzer_id").value), von: f.namedItem("von").value, bis: f.namedItem("bis").value };
  if (await hdSenden("pause", daten)) ev.target.reset();
});

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
hausdienstLaden();
setInterval(() => { if (!document.querySelector(".mehr[open]")) laden(); }, 30000);
