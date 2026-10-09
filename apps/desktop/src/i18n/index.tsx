/** Greek/English UI strings (NFR-ACC: el/en). Greek is the default; the choice is remembered. */
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

const en = {
  appName: "Panos",
  "nav.conversation": "Conversation",
  "nav.jobs": "Jobs",
  "nav.approvals": "Approvals",
  "nav.projects": "Projects",
  "nav.apps": "Applications",
  "nav.settings": "Settings",
  "nav.audit": "Audit",
  "nav.status": "Status",
  "status.connected": "Connected",
  "status.connecting": "Connecting…",
  "status.offline": "Node offline",
  "conv.placeholder": "Ask Panos… (Enter to send)",
  "conv.send": "Send",
  "conv.stop": "Stop",
  "conv.empty": "Ask anything, or try “list projects”.",
  "conv.you": "You",
  "conv.panos": "Panos",
  "conv.heard": "Heard",
  "conv.sources": "Sources",
  "conv.local": "local",
  "conv.cloud": "cloud",
  "voice.talk": "Hold to talk",
  "voice.listening": "Listening…",
  "voice.transcribing": "Transcribing…",
  "voice.off": "Voice off",
  "voice.stopTalking": "Stop talking",
  "jobs.empty": "No jobs yet.",
  "jobs.steps": "Steps",
  "jobs.result": "Result",
  "jobs.pause": "Pause",
  "jobs.resume": "Resume",
  "jobs.cancel": "Cancel",
  "jobs.rollback": "Roll back",
  "jobs.rollbackConfirm": "Restore the project files to the job's checkpoint?",
  "jobs.changed": "Changed files",
  "jobs.unresolved": "Unresolved",
  "approvals.empty": "Nothing waits for your approval.",
  "approvals.approve": "Approve",
  "approvals.reject": "Reject",
  "approvals.details": "Exact action",
  "approvals.expires": "Expires",
  "projects.empty": "No projects registered.",
  "projects.open": "Open",
  "projects.index": "Index",
  "projects.privacy": "Privacy",
  "apps.empty": "No supported applications found.",
  "apps.launch": "Launch",
  "settings.language": "Language",
  "settings.providers": "AI providers",
  "settings.usage": "AI usage this month",
  "settings.budget": "Monthly budget",
  "settings.voice": "Voice",
  "settings.wakeWord": "Wake word (always listening)",
  "settings.tts": "Speak replies",
  "settings.voiceNotifications": "Speak important notifications",
  "settings.setKey": "Set key",
  "settings.autostart": "Start Panos when I sign in",
  "settings.keySaved": "Key stored in the OS keychain.",
  "audit.verify": "Verify chain",
  "audit.ok": "Audit chain intact",
  "audit.broken": "Audit chain broken",
  "common.loading": "Loading…",
  "common.error": "Something went wrong",
  "common.retry": "Retry",
  "common.refresh": "Refresh",
} as const;

export type MessageKey = keyof typeof en;

const el: Record<MessageKey, string> = {
  appName: "Panos",
  "nav.conversation": "Συνομιλία",
  "nav.jobs": "Εργασίες",
  "nav.approvals": "Εγκρίσεις",
  "nav.projects": "Έργα",
  "nav.apps": "Εφαρμογές",
  "nav.settings": "Ρυθμίσεις",
  "nav.audit": "Καταγραφή",
  "nav.status": "Κατάσταση",
  "status.connected": "Συνδεδεμένο",
  "status.connecting": "Σύνδεση…",
  "status.offline": "Ο κόμβος δεν τρέχει",
  "conv.placeholder": "Ρώτα τον Panos… (Enter για αποστολή)",
  "conv.send": "Αποστολή",
  "conv.stop": "Διακοπή",
  "conv.empty": "Ρώτα οτιδήποτε ή δοκίμασε «δείξε μου τα projects».",
  "conv.you": "Εσύ",
  "conv.panos": "Panos",
  "conv.heard": "Άκουσα",
  "conv.sources": "Πηγές",
  "conv.local": "τοπικά",
  "conv.cloud": "cloud",
  "voice.talk": "Κράτα για ομιλία",
  "voice.listening": "Ακούω…",
  "voice.transcribing": "Μεταγραφή…",
  "voice.off": "Φωνή ανενεργή",
  "voice.stopTalking": "Σταμάτα να μιλάς",
  "jobs.empty": "Δεν υπάρχουν εργασίες ακόμα.",
  "jobs.steps": "Βήματα",
  "jobs.result": "Αποτέλεσμα",
  "jobs.pause": "Παύση",
  "jobs.resume": "Συνέχεια",
  "jobs.cancel": "Ακύρωση",
  "jobs.rollback": "Επαναφορά",
  "jobs.rollbackConfirm": "Επαναφορά των αρχείων του έργου στο σημείο ελέγχου της εργασίας;",
  "jobs.changed": "Αλλαγμένα αρχεία",
  "jobs.unresolved": "Εκκρεμότητες",
  "approvals.empty": "Τίποτα δεν περιμένει έγκριση.",
  "approvals.approve": "Έγκριση",
  "approvals.reject": "Απόρριψη",
  "approvals.details": "Ακριβής ενέργεια",
  "approvals.expires": "Λήγει",
  "projects.empty": "Δεν υπάρχουν καταχωρημένα έργα.",
  "projects.open": "Άνοιγμα",
  "projects.index": "Ευρετήριο",
  "projects.privacy": "Ιδιωτικότητα",
  "apps.empty": "Δεν βρέθηκαν υποστηριζόμενες εφαρμογές.",
  "apps.launch": "Εκκίνηση",
  "settings.language": "Γλώσσα",
  "settings.providers": "Πάροχοι AI",
  "settings.usage": "Χρήση AI αυτόν τον μήνα",
  "settings.budget": "Μηνιαίο όριο",
  "settings.voice": "Φωνή",
  "settings.wakeWord": "Λέξη αφύπνισης (πάντα σε ακρόαση)",
  "settings.tts": "Εκφώνηση απαντήσεων",
  "settings.voiceNotifications": "Εκφώνηση σημαντικών ειδοποιήσεων",
  "settings.setKey": "Ορισμός κλειδιού",
  "settings.autostart": "Εκκίνηση του Panos κατά τη σύνδεση",
  "settings.keySaved": "Το κλειδί αποθηκεύτηκε στο keychain του λειτουργικού.",
  "audit.verify": "Έλεγχος αλυσίδας",
  "audit.ok": "Η αλυσίδα καταγραφής είναι ακέραιη",
  "audit.broken": "Η αλυσίδα καταγραφής έχει σπάσει",
  "common.loading": "Φόρτωση…",
  "common.error": "Κάτι πήγε στραβά",
  "common.retry": "Ξανά",
  "common.refresh": "Ανανέωση",
};

export type Lang = "el" | "en";
const dictionaries: Record<Lang, Record<MessageKey, string>> = { en, el };

interface I18n {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: (key: MessageKey) => string;
}

const I18nContext = createContext<I18n | null>(null);
const STORAGE_KEY = "panos.lang";

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
  const value = useMemo<I18n>(
    () => ({ lang, setLang, t: (key) => dictionaries[lang][key] ?? key }),
    [lang, setLang],
  );
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18n {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n outside I18nProvider");
  return ctx;
}

export const MESSAGES = { en, el };
