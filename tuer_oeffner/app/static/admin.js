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
  hd_bewertung: "Hausdienst bewertet", admin_hd_bewertung: "Hausdienst bewertet (Verwaltung)",
  hd_anruf_absage: "Hausdienst-Anruf abgesagt", hd_anruf_wieder: "Hausdienst-Anruf wieder angenommen",
  admin_hd_anrufe_an: "Hausdienst-Anrufe für Person an", admin_hd_anrufe_aus: "Hausdienst-Anrufe für Person aus",
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
const hd = { personen: [], aufgaben: [], zugaenge: [] };

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

function loeschKnopf(beschriftung, fn) {
  const k = knopf("×", fn, "gefahr");
  k.setAttribute("aria-label", beschriftung);
  return k;
}

/* ---------- Plan: Vorlage und die nächsten zwei Wochen in einer Tabelle ----------
   Ansicht "vorlage" = Wochenplan, "w0"/"w1" = die 14 Tage in zwei Blöcken.
   Das Popover zum Bearbeiten ist ein einziges Element, das nach jedem
   Neuladen an derselben Zelle wieder geöffnet wird. */

const PERSONEN_FARBEN = ["#2f7fd0", "#d9822b", "#2e9d5f", "#b24fb5", "#d04a4a", "#1fa3a3", "#8a7a1f", "#6a63d6"];
const plan = { ansicht: "vorlage", offen: null, daten: null };
try {
  plan.ansicht = localStorage.getItem("hd-ansicht") || "vorlage";
} catch (e) { /* ohne Speicher bleibt es bei der Vorlage */ }

const personFarbe = (id) => PERSONEN_FARBEN[Math.max(0, hd.personen.findIndex((p) => p.id === id)) % PERSONEN_FARBEN.length];

function chip(name, person_id, zusatz, klasse = "") {
  const c = el("span", name, "hd-chip " + klasse);
  c.style.setProperty("--pf", personFarbe(person_id));
  if (zusatz) c.append(el("small", " " + zusatz));
  return c;
}

const kurzDatum = (iso) => `${Number(iso.slice(8))}.${Number(iso.slice(5, 7))}.`;

function ansichten(d) {
  const bloecke = [d.tage.slice(0, 7), d.tage.slice(7, 14)].filter((b) => b.length);
  const liste = [{ id: "vorlage", text: "Jede Woche (Vorlage)" }];
  bloecke.forEach((b, i) => {
    const von = b[0].datum, bis = b[b.length - 1].datum;
    const text = von.slice(5, 7) === bis.slice(5, 7)
      ? `${Number(von.slice(8))}.–${kurzDatum(bis)}` : `${kurzDatum(von)}–${kurzDatum(bis)}`;
    liste.push({ id: "w" + i, text, tage: b });
  });
  return liste;
}

/* Inhalt einer Zelle: Aufgabe an Spalte i in der gewählten Ansicht */
function zelleInhalt(d, ansicht, i, aufgabe) {
  if (!ansicht.tage) {
    const items = d.plan.filter((p) => p.wochentag === i && p.aufgabe_id === aufgabe.id)
      .map((p) => ({ person_id: p.person_id, name: p.name, plan_id: p.id }));
    return { items, tag: d.wochentage[i], datum: null, abweichend: false };
  }
  const t = ansicht.tage[i];
  const items = t.dienste.filter((x) => x.aufgabe_id === aufgabe.id).map((x) => {
    const urspruenglich = x.entfaellt ? d.plan.find((p) => p.id === x.plan_id) : null;
    return { ...x, name: x.name || (urspruenglich && urspruenglich.name) || "?" };
  });
  return { items, tag: t.tag, datum: t.datum, abweichend: items.some((x) => x.abweichung_id) };
}

function zelleKnopf(d, ansicht, i, aufgabe) {
  const z = zelleInhalt(d, ansicht, i, aufgabe);
  const schluessel = `${ansicht.id}|${i}|${aufgabe.id}`;
  const k = el("button", null, "hd-zelle" + (z.abweichend ? " abweichend" : ""));
  k.type = "button";
  k.dataset.schluessel = schluessel;
  k.setAttribute("aria-label", `${aufgabe.name} am ${z.tag} bearbeiten`);
  k.setAttribute("aria-expanded", plan.offen === schluessel ? "true" : "false");
  if (!z.items.length) k.append(el("span", "–", "leise"));
  z.items.forEach((x) => {
    if (x.entfaellt) k.append(chip(x.name, x.person_id, "entfällt", "aus"));
    else if (x.pausiert) k.append(chip(x.name, x.person_id, "Pause", "pause"));
    else k.append(chip(x.name, x.person_id));
  });
  k.onclick = () => {
    if (plan.offen === schluessel) return popSchliessen();
    plan.offen = schluessel;
    popZeigen();
  };
  return k;
}

function planZeigen(d) {
  plan.daten = d;
  const alle = ansichten(d);
  let ansicht = alle.find((a) => a.id === plan.ansicht);
  if (!ansicht) ansicht = alle[0];
  plan.ansicht = ansicht.id;
  plan.aktiv = ansicht;

  const umschalter = el("div", null, "hd-umschalter");
  umschalter.setAttribute("role", "group");
  umschalter.setAttribute("aria-label", "Ansicht");
  alle.forEach((a) => {
    const k = knopf(a.text, () => {
      plan.ansicht = a.id;
      try { localStorage.setItem("hd-ansicht", a.id); } catch (e) { /* egal */ }
      popSchliessen();
      planZeigen(plan.daten);
    }, a.id === ansicht.id ? "haupt" : "");
    k.setAttribute("aria-pressed", a.id === ansicht.id ? "true" : "false");
    umschalter.append(k);
  });

  const spalten = ansicht.tage
    ? ansicht.tage.map((t) => t.tag.replace(", ", " "))
    : d.wochentage;

  // Tabelle für breite Bildschirme
  const tabelle = el("table", null, "hd-tabelle");
  const kopf = el("tr");
  kopf.append(el("th", "Aufgabe"));
  spalten.forEach((s) => { const th = el("th", s); th.scope = "col"; kopf.append(th); });
  const thead = el("thead");
  thead.append(kopf);
  const tbody = el("tbody");
  d.aufgaben.forEach((a) => {
    const tr = el("tr");
    const th = el("th", a.name);
    th.scope = "row";
    tr.append(th);
    spalten.forEach((_, i) => { const td = el("td"); td.append(zelleKnopf(d, ansicht, i, a)); tr.append(td); });
    tbody.append(tr);
  });
  tabelle.append(thead, tbody);
  const rahmen = el("div", null, "hd-rahmen");
  rahmen.append(tabelle);

  // Liste pro Tag für das Handy
  const tage = el("div", null, "hd-tageliste");
  spalten.forEach((s, i) => {
    const block = el("div", null, "hd-tagblock");
    block.append(el("h4", s));
    d.aufgaben.forEach((a) => {
      const zeile = el("div", null, "hd-tagzeile");
      zeile.append(el("span", a.name, "hd-aufgabe"), zelleKnopf(d, ansicht, i, a));
      block.append(zeile);
    });
    tage.append(block);
  });

  // Zählzeile
  const zaehler = el("p", null, "hd-zaehler-zeile");
  zaehler.append("Dienste: ");
  d.personen.forEach((p, n) => {
    const anzahl = ansicht.tage
      ? ansicht.tage.reduce((s, t) => s + t.dienste.filter((x) => x.person_id === p.id && !x.entfaellt && !x.pausiert).length, 0)
      : d.plan.filter((x) => x.person_id === p.id).length;
    if (n) zaehler.append(" · ");
    const s = el("span", `${p.name} ${anzahl}`, "hd-zaehl");
    s.style.setProperty("--pf", personFarbe(p.id));
    zaehler.append(s);
  });
  if (!d.personen.length) zaehler.hidden = true;

  const leer = !d.aufgaben.length;
  $("#hd-plan").replaceChildren(umschalter, ...(leer ? [] : [rahmen, tage, zaehler]));
  if (plan.offen) popZeigen();
}

/* ---------- Popover einer Zelle ---------- */

function popSchliessen() {
  plan.offen = null;
  const p = $("#hd-pop");
  if (p) p.remove();
  document.querySelectorAll(".hd-zelle[aria-expanded='true']").forEach((k) => k.setAttribute("aria-expanded", "false"));
}

function popSetzen(d, ansicht, i, aufgabe, z, person, an) {
  if (!ansicht.tage) {
    if (an) return hdSenden("plan", { wochentag: i, person_id: person.id, aufgabe_id: aufgabe.id });
    const x = z.items.find((y) => y.person_id === person.id);
    return hdSenden(`plan/${x.plan_id}/loeschen`, {});
  }
  if (an) {
    const frei = z.items.find((x) => x.entfaellt && x.plan_id);
    return hdSenden("tag", frei
      ? { datum: z.datum, plan_id: frei.plan_id, person_id: person.id }
      : { datum: z.datum, person_id: person.id, aufgabe_id: aufgabe.id });
  }
  const x = z.items.find((y) => y.person_id === person.id && !y.entfaellt);
  return x.plan_id
    ? hdSenden("tag", { datum: z.datum, plan_id: x.plan_id, person_id: null })
    : hdSenden(`tag/${x.abweichung_id}/loeschen`, {});
}

async function popZuruecksetzen(z) {
  try {
    for (const x of z.items.filter((y) => y.abweichung_id)) await api(`admin/hausdienst/tag/${x.abweichung_id}/loeschen`, {});
    melde("");
  } catch (e) {
    melde(e.message);
  }
  hausdienstLaden();
}

function popZeigen() {
  const d = plan.daten;
  const [ansichtId, spalte, aufgabeId] = plan.offen.split("|");
  const ansicht = ansichten(d).find((a) => a.id === ansichtId);
  const aufgabe = d.aufgaben.find((a) => a.id === Number(aufgabeId));
  const anker = [...document.querySelectorAll(".hd-zelle")].filter((k) => k.dataset.schluessel === plan.offen && k.offsetParent)[0];
  if (!ansicht || !aufgabe || !anker) return popSchliessen();
  const i = Number(spalte);
  const z = zelleInhalt(d, ansicht, i, aufgabe);

  const pop = el("div", null, "hd-pop");
  pop.id = "hd-pop";
  pop.setAttribute("role", "dialog");
  pop.setAttribute("aria-label", `${aufgabe.name} am ${z.tag}`);
  pop.append(el("h4", `${aufgabe.name} · ${z.tag}`));
  const vergeben = new Set(z.items.filter((x) => !x.entfaellt).map((x) => x.person_id));
  d.personen.forEach((p) => {
    const l = el("label", null, "hd-wahl");
    const h = el("input");
    h.type = "checkbox";
    h.checked = vergeben.has(p.id);
    h.onchange = () => popSetzen(d, ansicht, i, aufgabe, z, p, h.checked);
    l.append(h, chip(p.name, p.id));
    pop.append(l);
  });
  if (!d.personen.length) pop.append(el("p", "Noch niemand eingetragen.", "leise"));
  const fuss = el("div", null, "hd-pop-fuss");
  if (z.abweichend) fuss.append(knopf("↺ wie Vorlage", () => popZuruecksetzen(z)));
  fuss.append(knopf("Schließen", popSchliessen));
  pop.append(fuss);
  const alt = $("#hd-pop");
  if (alt) alt.remove();
  document.body.append(pop);
  document.querySelectorAll(".hd-zelle").forEach((k) => k.setAttribute("aria-expanded", k.dataset.schluessel === plan.offen ? "true" : "false"));
  popPositionieren();
}

function popPositionieren() {
  const pop = $("#hd-pop");
  if (!pop) return;
  const anker = [...document.querySelectorAll(".hd-zelle")].filter((k) => k.dataset.schluessel === plan.offen && k.offsetParent)[0];
  if (!anker) return popSchliessen();
  if (window.matchMedia("(max-width: 640px)").matches) {
    pop.style.left = pop.style.top = ""; // unten angedockt (CSS)
    return;
  }
  const r = anker.getBoundingClientRect();
  const h = pop.offsetHeight, b = pop.offsetWidth;
  pop.style.left = Math.max(8, Math.min(r.left, window.innerWidth - b - 8)) + "px";
  pop.style.top = (r.bottom + 4 + h > window.innerHeight && r.top - h - 4 > 0 ? r.top - h - 4 : r.bottom + 4) + "px";
}

const HD_TAUSCH = { offen: "wartet", ja: "übernimmt", nein: "kann nicht", zurueckgezogen: "zurückgezogen", abgelaufen: "abgelaufen" };

function anfragenZeigen(d) {
  const teile = d.anfragen.map((a) => {
    const z = el("div", null, "hd-antrag");
    z.append(el("span", `Tausch: ${a.von_name} fragt ${a.an_name} – ${a.dienst}`), el("small", zeit(a.angelegt), "leise"),
      el("span", HD_TAUSCH[a.status] || a.status, "status " + (a.status === "ja" ? "aktiv" : a.status === "offen" ? "wartet" : "neu")));
    return z;
  });
  $("#hd-anfragen").replaceChildren(...teile);
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

/* Auswahl des Zugangs: frei oder schon mit dieser Person verknuepft */
function zugangListe(eigener) {
  const vergeben = new Set(hd.personen.map((p) => p.benutzer_id).filter((b) => b && b !== eigener));
  const s = liste("benutzer_id", hd.zugaenge.filter((z) => !vergeben.has(z.id))
    .map((z) => [z.id, z.name + (z.status === "aktiv" ? "" : " (nicht freigegeben)")]), "ohne Zugang");
  s.value = eigener ?? "";
  s.setAttribute("aria-label", "Zugang");
  return s;
}

function personenZeigen(d) {
  $("#hd-personen").replaceChildren(...d.personen.map((p) => {
    const f = el("form", null, "hd-zeile");
    const name = el("input");
    Object.assign(name, { name: "name", value: p.name, maxLength: 40, required: true });
    name.setAttribute("aria-label", "Name");
    const zugang = zugangListe(p.benutzer_id);
    f.append(name, zugang, el("button", "Speichern", "knopf klein"),
      loeschKnopf(`${p.name} löschen`, () => hdSenden(`person/${p.id}/loeschen`, {},
        `${p.name} aus dem Hausdienst löschen? Die Dienste im Plan, Pausen und die Telefonnummer verschwinden mit.`)));
    f.onsubmit = (ev) => {
      ev.preventDefault();
      hdSenden("person", { id: p.id, name: name.value, benutzer_id: zugang.value ? Number(zugang.value) : null });
    };
    return f;
  }));
  if (!d.personen.length) $("#hd-personen").append(el("p", "Noch niemand eingetragen.", "leise"));
  const neu = $('#hd-person-neu [name="benutzer_id"]');
  const gewaehlt = neu.value;
  neu.replaceWith(zugangListe(null));
  $('#hd-person-neu [name="benutzer_id"]').value = gewaehlt;
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
  // Die Auswahl bleibt stehen, auch wenn die Liste neu geladen wird
  const wer = $('#hd-pause-neu [name="person_id"]');
  const gewaehlt = wer.value;
  wer.replaceChildren(new Option("Wer?", ""), ...personenListe().map(([id, n]) => new Option(n, id)));
  wer.value = gewaehlt;
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
  ["Person", "Anrufe", "Nummer", "Jeden Tag", ...KURZ_TAGE, ""].forEach((t) => kopf.append(el("th", t)));
  const thead = el("thead");
  thead.append(kopf);
  const tbody = el("tbody");
  d.personen.forEach((p) => {
    const t = d.telefon[p.id] || { nummer: "", zeit: "", tage: {}, anrufe: true, abgesagt: "" };
    const tr = el("tr");
    const nummer = eingabe({ value: t.nummer, inputMode: "tel", placeholder: "0170…" }, `Nummer von ${p.name}`);
    const uhr = eingabe({ type: "time", value: t.zeit }, `Uhrzeit für ${p.name}`);
    const tage = KURZ_TAGE.map((k, i) => eingabe({ type: "time", value: t.tage[i] || "" }, `${p.name} am ${d.wochentage[i]} abweichend`));
    const speichern = knopf("Speichern", () => hdSenden("telefon", {
      person_id: p.id, nummer: nummer.value, zeit: uhr.value,
      tage: Object.fromEntries(tage.map((x, i) => [i, x.value]).filter(([, v]) => v)),
    }));
    const test = knopf("Testanruf", async () => {
      if (!confirm(`${p.name} jetzt anrufen?`)) return;
      try {
        const r = await api("admin/hausdienst/testanruf", { person_id: p.id });
        melde(`Ruft ${p.name} an: „${r.ansage}“`, "ok");
      } catch (e) {
        melde(e.message);
      }
      hausdienstLaden();
    });
    test.disabled = !a.moeglich || !t.nummer;
    const aktionen = el("td");
    aktionen.append(speichern, test);
    // Schalter wie bei den Rechten, sofort gespeichert
    const schalter = el("button", null, "recht klein");
    schalter.type = "button";
    schalter.setAttribute("role", "switch");
    schalter.setAttribute("aria-checked", String(t.anrufe));
    schalter.setAttribute("aria-label", `Anrufe für ${p.name}`);
    schalter.append(el("span", null, "schalter"));
    schalter.onclick = () => hdSenden("anrufe_person", { person_id: p.id, an: !t.anrufe });
    const anrufTd = el("td");
    anrufTd.append(schalter);
    if (t.anrufe && t.abgesagt) anrufTd.append(el("small", `${t.abgesagt} abgesagt`, "abgesagt"));
    tr.append(el("td", p.name), anrufTd, ...[nummer, uhr, ...tage].map((x) => {
      const td = el("td");
      td.append(x);
      return td;
    }), aktionen);
    tbody.append(tr);
  });
  tabelle.append(thead, tbody);
  const rahmen = el("div", null, "tabelle-rahmen");
  rahmen.append(tabelle);
  teile.push(rahmen, el("p", "„Jeden Tag“ gilt für alle Tage mit Dienst; in Mo–So nur eintragen, wenn es an dem Tag anders sein soll. Ohne Uhrzeit oder Nummer wird nicht angerufen. Wer einen Zugang hat, kann seine Zeit auch selbst in der App einstellen.", "leise"));

  if (d.anrufliste.length) {
    const liste = el("table");
    const k = el("tr");
    ["Wann", "Wer", "Anruf", "Ergebnis"].forEach((t) => k.append(el("th", t)));
    const lk = el("thead");
    lk.append(k);
    const lb = el("tbody");
    d.anrufliste.forEach((r) => {
      const tr = el("tr");
      const um = r.bestaetigt && new Date(r.bestaetigt * 1000).toLocaleTimeString("de-DE", { timeStyle: "short" });
      const ergebnis = r.fehler ? "Fehler: " + r.fehler
        : r.versuch < 0 ? (um ? "Antwort per Taste um " + um : "keine Antwort per Taste")
          : um ? "Taste 1 um " + um : "keine Taste 1 (Mailbox oder nicht abgenommen)";
      tr.append(el("td", zeit(r.zeit)), el("td", r.name), el("td", { "-1": "Tausch", 0: "Test", 1: "1.", 2: "2." }[r.versuch] ?? r.versuch),
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

/* ---------- Bewertungen ---------- */

const BW_KNOEPFE = [[3, "top"], [2, "gut"], [1, "nicht so gut"], [0, "gar nicht"]];
const BW_ZEITRAEUME = [["woche", "Diese Woche"], ["monat", "Dieser Monat"], ["gesamt", "Insgesamt"]];

function bewertungZelle(w) {
  const td = el("td");
  if (!w.anzahl) td.append(el("span", "–", "leise"));
  else {
    td.append(el("strong", `Ø ${w.durchschnitt}`), el("span", ` (${w.anzahl})`, "leise"));
    // verteilung: top, gut, nicht so gut, gar nicht
    const teile = BW_KNOEPFE.map(([, text], i) => (w.verteilung[i] ? `${w.verteilung[i]} ${text}` : "")).filter(Boolean);
    td.append(el("small", teile.join(" · "), "bw-verteilung"));
  }
  if (w.unbewertet) td.append(el("small", `${w.unbewertet} ohne Bewertung`, "bw-verteilung leise"));
  return td;
}

function bewertungenZeigen(d) {
  const b = d.bewertungen;
  // Gregor bewertet ab 16 Uhr mit, wie die Familie in der App
  const kopf = el("div", null, "bw-heute");
  if (!b.heute.offen) kopf.append(el("p", `Bewerten kannst du die heutigen Dienste ab ${b.heute.ab} Uhr.`, "leise"));
  else if (!b.heute.dienste.length) kopf.append(el("p", "Heute gibt es keine Dienste zu bewerten.", "leise"));
  else {
    kopf.append(el("p", "Heute bewerten (als „Admin“):", "leise"));
    for (const x of b.heute.dienste) {
      const z = el("div", null, "bw-dienst");
      const wer = el("span", null, "bw-wer");
      wer.append(chip(x.name, d.personen.find((p) => p.name === x.name)?.id), el("span", " " + x.aufgabe));
      const reihe = el("div", null, "bw-knoepfe");
      for (const [wert, text] of BW_KNOEPFE) {
        const k = knopf(text, () => bewertungSenden(x.dienst, wert), x.wert === wert ? "haupt" : "");
        k.setAttribute("aria-pressed", String(x.wert === wert));
        reihe.append(k);
      }
      z.append(wer, reihe);
      kopf.append(z);
    }
  }
  $("#hd-bewerten").replaceChildren(kopf);
  const tabelle = el("table", null, "bw-tabelle");
  const kk = el("thead");
  const kr = el("tr");
  kr.append(el("th", "Person"), ...BW_ZEITRAEUME.map(([, t]) => el("th", t)));
  kk.append(kr);
  const tk = el("tbody");
  for (const p of b.personen) {
    const tr = el("tr");
    const name = el("th");
    name.append(chip(p.name, p.id));
    tr.append(name, ...BW_ZEITRAEUME.map(([z]) => bewertungZelle(p[z])));
    tk.append(tr);
  }
  tabelle.append(kk, tk);
  const rahmen = el("div", null, "tabelle-rahmen");
  rahmen.append(tabelle);
  $("#hd-bewertung").replaceChildren(b.personen.length ? rahmen : el("p", "Noch keine Personen.", "leise"));
  const lt = el("table", null, "bw-tabelle");
  const lk = el("thead");
  const lkr = el("tr");
  lkr.append(...["Tag", "Dienst", "Bewertet", "Von", "Wert"].map((t) => el("th", t)));
  lk.append(lkr);
  const lb = el("tbody");
  for (const r of b.einzeln) {
    const tr = el("tr");
    const person = el("td");
    person.append(chip(r.name, d.personen.find((p) => p.name === r.name)?.id));
    tr.append(el("td", r.tag), el("td", r.aufgabe), person, el("td", r.von), el("td", r.wert_text, "bw-wert bw-w" + r.wert));
    lb.append(tr);
  }
  lt.append(lk, lb);
  const lr = el("div", null, "tabelle-rahmen");
  lr.append(lt);
  $("#hd-bewertung-liste").replaceChildren(b.einzeln.length ? lr : el("p", "Noch keine Bewertungen.", "leise"));
}

async function bewertungSenden(dienst, wert) {
  try {
    await api("admin/hausdienst/bewertung", { dienst, wert });
    melde("");
  } catch (e) {
    melde(e.message);
  }
  hausdienstLaden();
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
  hd.zugaenge = d.zugaenge;
  const hinweise = [];
  if (!d.personen.length) hinweise.push("Zuerst unten die Personen anlegen – mit oder ohne Zugang zum Türöffner.");
  if (!d.aufgaben.length) hinweise.push("Zuerst unten die Aufgaben anlegen (z. B. Küche → Küchendienst), dann im Plan eintragen, wer wann dran ist.");
  $("#hd-hinweise").replaceChildren(...hinweise.map((w) => el("p", w, "hinweis")));
  antraegeZeigen(d);
  anfragenZeigen(d);
  planZeigen(d);
  personenZeigen(d);
  aufgabenZeigen(d);
  pausenZeigen(d);
  bewertungenZeigen(d);
  anrufeZeigen(d);
}

$("#hd-aufgabe-neu").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target.elements;
  if (await hdSenden("aufgabe", { name: f.namedItem("name").value, ansage: f.namedItem("ansage").value })) ev.target.reset();
});

$("#hd-person-neu").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target.elements;
  const zugang = f.namedItem("benutzer_id").value;
  if (await hdSenden("person", { name: f.namedItem("name").value, benutzer_id: zugang ? Number(zugang) : null })) ev.target.reset();
});

$("#hd-pause-neu").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target.elements;
  const daten = { person_id: Number(f.namedItem("person_id").value), von: f.namedItem("von").value, bis: f.namedItem("bis").value };
  if (await hdSenden("pause", daten)) ev.target.reset();
});

// Ein offenes "Mehr"-Menue schliesst bei einem Klick daneben
document.addEventListener("click", (ev) => {
  document.querySelectorAll(".mehr[open]").forEach((m) => { if (!m.contains(ev.target)) m.open = false; });
  // Plan-Popover schliesst bei einem Klick daneben
  if (plan.offen && !ev.target.closest(".hd-pop, .hd-zelle")) popSchliessen();
});
document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && plan.offen) popSchliessen(); });
window.addEventListener("resize", popPositionieren);
window.addEventListener("scroll", popPositionieren, true);

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
