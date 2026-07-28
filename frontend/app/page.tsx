import { redirect } from "next/navigation";

import { getLastOrganizationId, getSession } from "@/lib/session";

export default async function RootPage() {
  const session = await getSession();
  if (!session) {
    redirect("/login");
  }

  const organizationId = await getLastOrganizationId();
  redirect(organizationId ? `/org/${organizationId}/dashboard` : "/login");
}
