import { TableSkeleton } from "@/components/ui/spinner";

export default function OrgSectionLoading() {
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <div className="h-7 w-48 animate-pulse rounded-md bg-slate-200 dark:bg-slate-800" />
        <div className="h-4 w-72 animate-pulse rounded-md bg-slate-100 dark:bg-slate-800/60" />
      </div>
      <div className="rounded-lg border border-slate-200 dark:border-slate-800">
        <TableSkeleton rows={6} columns={4} />
      </div>
    </div>
  );
}
