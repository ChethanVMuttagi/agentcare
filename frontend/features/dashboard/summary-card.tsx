import Link from "next/link";
import type { ReactNode } from "react";

import { Card, CardHeader, CardTitle } from "@/components/ui/card";

export function SummaryCard({
  title,
  href,
  linkLabel = "View all",
  children,
}: {
  title: string;
  href: string;
  linkLabel?: string;
  children: ReactNode;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <Link href={href} className="text-sm font-medium text-slate-600 hover:underline dark:text-slate-300">
          {linkLabel}
        </Link>
      </CardHeader>
      <div className="divide-y divide-slate-100 dark:divide-slate-800">{children}</div>
    </Card>
  );
}
