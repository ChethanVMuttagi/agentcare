import type { Role } from "@/types/api";

export interface NavItem {
  segment: string;
  label: string;
  /** Roles that see this nav item — kept in sync with the role
   * restrictions in the backend API catalog for each resource (not every
   * role reaches every endpoint; e.g. `supervisor` is scoped to
   * approvals only, per the Approvals endpoints being the only ones that
   * list SUPERVISOR as an allowed role). This is a UI convenience only —
   * see `lib/session.ts` `getRoleHint` docs for why it carries no
   * authorization weight. */
  roles: Role[];
}

export const NAV_ITEMS: NavItem[] = [
  { segment: "dashboard", label: "Dashboard", roles: ["admin", "staff", "patient", "supervisor"] },
  { segment: "patients", label: "Patients", roles: ["admin", "staff"] },
  { segment: "appointments", label: "Appointments", roles: ["admin", "staff", "patient"] },
  { segment: "assistant", label: "AI Assistant", roles: ["admin", "staff", "patient"] },
  { segment: "demo", label: "Demo Mode", roles: ["admin", "staff"] },
  { segment: "workflows", label: "Workflows", roles: ["admin", "staff", "patient"] },
  { segment: "approvals", label: "Approvals", roles: ["admin", "staff", "supervisor"] },
  { segment: "documents", label: "Documents", roles: ["admin", "staff", "patient"] },
  { segment: "analytics", label: "Analytics", roles: ["admin", "staff"] },
  { segment: "architecture", label: "Architecture", roles: ["admin", "staff", "patient", "supervisor"] },
];
