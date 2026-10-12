/** Greek/English UI strings. Greek is the default; the choice is remembered. */
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

const en = {
  appName: "Panos hub",
  "nav.overview": "Overview",
  "nav.nodes": "PCs",
  "nav.jobs": "Jobs",
  "nav.approvals": "Approvals",
  "nav.dispatch": "Send a job",
  "nav.usage": "AI usage",
  "nav.traceability": "Traceability",
  "nav.audit": "Hub log",
  "auth.title": "Sign in to the Panos hub",
  "auth.signIn": "Sign in with a passkey",
  "auth.setup": "Set up a passkey",
  "auth.code": "Setup code (from `panos hub passkey-code`)",
  "auth.name": "Your name",
  "auth.unsupported": "This browser cannot use passkeys. Use a current Chrome, Edge, Firefox or Safari over HTTPS.",
  "auth.signOut": "Sign out",
  "common.refresh": "Refresh",
  "common.loading": "Loading…",
  "common.none": "Nothing yet.",
  "common.node": "PC",
  "common.project": "Project",
  "common.status": "Status",
  "common.when": "When",
  "overview.online": "PCs online",
  "overview.running": "Running jobs",
  "overview.pending": "Waiting for you",
  "overview.day": "Jobs in the last day",
  "nodes.platform": "Platform",
  "nodes.lastSeen": "Last seen",
  "nodes.projects": "Projects",
  "nodes.revoke": "Revoke",
  "nodes.revokeConfirm": "Revoke this PC? It is disconnected at once and must join again with a new code.",
  "nodes.addPc": "Add a PC",
  "nodes.addHelp": "Run this on the new PC:",
  "jobs.objective": "Objective",
  "jobs.steps": "Steps",
  "jobs.pause": "Pause",
  "jobs.resume": "Resume",
  "jobs.cancel": "Cancel",
  "jobs.liveOnly": "Shown live from the PC (local-only project: not stored on the hub).",
  "jobs.offline": "The PC is offline; only stored metadata is shown.",
  "approvals.action": "Action",
  "approvals.target": "Target",
  "approvals.hash": "Payload SHA-256",
  "approvals.approve": "Approve",
  "approvals.reject": "Reject",
  "approvals.details": "Show the exact action",
  "dispatch.kind": "Kind",
  "dispatch.kind.coding": "Coding job",
  "dispatch.kind.command": "Command",
  "dispatch.kind.research": "Research",
  "dispatch.objective": "What should it do?",
  "dispatch.command": "Command (arguments separated by spaces)",
  "dispatch.send": "Send",
  "dispatch.target": "It will run on",
  "dispatch.policy": "It runs under that PC's own permissions; risky steps wait for approval.",
  "dispatch.accepted": "Accepted as job",
  "dispatch.refused": "Refused",
  "usage.days": "Last 30 days",
  "usage.cost": "Cost",
  "usage.calls": "Calls",
  "trace.load": "Show",
  "audit.verify": "Verify chain",
  "audit.ok": "Hub log intact",
  "audit.broken": "Hub log broken at",
} as const;

export type MessageKey = keyof typeof en;

const el: Record<MessageKey, string> = {
  appName: "Panos hub",
  "nav.overview": "Επισκόπηση",
  "nav.nodes": "Υπολογιστές",
  "nav.jobs": "Εργασίες",
  "nav.approvals": "Εγκρίσεις",
  "nav.dispatch": "Αποστολή εργασίας",
  "nav.usage": "Χρήση AI",
  "nav.traceability": "Ιχνηλασιμότητα",
  "nav.audit": "Καταγραφή hub",
  "auth.title": "Σύνδεση στο Panos hub",
  "auth.signIn": "Σύνδεση με passkey",
  "auth.setup": "Ρύθμιση passkey",
  "auth.code": "Κωδικός ρύθμισης (από `panos hub passkey-code`)",
  "auth.name": "Το όνομά σου",
  "auth.unsupported": "Αυτός ο browser δεν υποστηρίζει passkeys. Χρησιμοποίησε πρόσφατο Chrome, Edge, Firefox ή Safari μέσω HTTPS.",
  "auth.signOut": "Αποσύνδεση",
  "common.refresh": "Ανανέωση",
  "common.loading": "Φόρτωση…",
  "common.none": "Τίποτα ακόμα.",
  "common.node": "Υπολογιστής",
  "common.project": "Έργο",
  "common.status": "Κατάσταση",
  "common.when": "Πότε",
  "overview.online": "Συνδεδεμένοι υπολογιστές",
  "overview.running": "Εργασίες σε εξέλιξη",
  "overview.pending": "Περιμένουν εσένα",
  "overview.day": "Εργασίες τελευταίας ημέρας",
  "nodes.platform": "Πλατφόρμα",
  "nodes.lastSeen": "Τελευταία επαφή",
  "nodes.projects": "Έργα",
  "nodes.revoke": "Ανάκληση",
  "nodes.revokeConfirm": "Ανάκληση αυτού του υπολογιστή; Αποσυνδέεται αμέσως και χρειάζεται νέο κωδικό για να ξανασυνδεθεί.",
  "nodes.addPc": "Προσθήκη υπολογιστή",
  "nodes.addHelp": "Εκτέλεσε στον νέο υπολογιστή:",
  "jobs.objective": "Στόχος",
  "jobs.steps": "Βήματα",
  "jobs.pause": "Παύση",
  "jobs.resume": "Συνέχεια",
  "jobs.cancel": "Ακύρωση",
  "jobs.liveOnly": "Εμφανίζεται ζωντανά από τον υπολογιστή (έργο μόνο-τοπικό: δεν αποθηκεύεται στο hub).",
  "jobs.offline": "Ο υπολογιστής είναι εκτός σύνδεσης· φαίνονται μόνο τα αποθηκευμένα στοιχεία.",
  "approvals.action": "Ενέργεια",
  "approvals.target": "Στόχος",
  "approvals.hash": "SHA-256 περιεχομένου",
  "approvals.approve": "Έγκριση",
  "approvals.reject": "Απόρριψη",
  "approvals.details": "Δείξε την ακριβή ενέργεια",
  "dispatch.kind": "Είδος",
  "dispatch.kind.coding": "Εργασία κώδικα",
  "dispatch.kind.command": "Εντολή",
  "dispatch.kind.research": "Έρευνα",
  "dispatch.objective": "Τι να κάνει;",
  "dispatch.command": "Εντολή (ορίσματα χωρισμένα με κενό)",
  "dispatch.send": "Αποστολή",
  "dispatch.target": "Θα εκτελεστεί στον",
  "dispatch.policy": "Εκτελείται με τα δικαιώματα εκείνου του υπολογιστή· τα επικίνδυνα βήματα περιμένουν έγκριση.",
  "dispatch.accepted": "Έγινε δεκτή ως εργασία",
  "dispatch.refused": "Απορρίφθηκε",
  "usage.days": "Τελευταίες 30 ημέρες",
  "usage.cost": "Κόστος",
  "usage.calls": "Κλήσεις",
  "trace.load": "Εμφάνιση",
  "audit.verify": "Έλεγχος αλυσίδας",
  "audit.ok": "Η καταγραφή του hub είναι ακέραιη",
  "audit.broken": "Η καταγραφή του hub έχει σπάσει στο",
};

export type Lang = "el" | "en";
const dictionaries: Record<Lang, Record<MessageKey, string>> = { en, el };

interface I18n {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: (key: MessageKey) => string;
}

const I18nContext = createContext<I18n | null>(null);
const STORAGE_KEY = "panos.hub.lang";

function initialLang(): Lang {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === "el" || saved === "en") return saved;
  } catch {
    /* storage unavailable */
  }
  return "el";
}

export function I18nProvider({ children, lang: fixed }: { children: ReactNode; lang?: Lang }) {
  const [lang, setLangState] = useState<Lang>(fixed ?? initialLang());
  const setLang = useCallback((next: Lang) => {
    setLangState(next);
    document.documentElement.lang = next;
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* storage unavailable */
    }
  }, []);
  const value = useMemo<I18n>(() => ({ lang, setLang, t: (key) => dictionaries[lang][key] ?? key }), [lang, setLang]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18n {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n outside I18nProvider");
  return ctx;
}

export const MESSAGES = { en, el };
