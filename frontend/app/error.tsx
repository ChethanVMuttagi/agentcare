"use client";

import { useEffect } from "react";

import { Button } from "@/components/ui/button";
import { describeError } from "@/lib/errors";

export default function RootError({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string };
  unstable_retry: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 py-24 text-center">
      <p className="text-lg font-semibold text-slate-900 dark:text-slate-100">Something went wrong</p>
      <p className="max-w-sm text-sm text-slate-500 dark:text-slate-400">{describeError(error)}</p>
      <Button variant="secondary" onClick={() => unstable_retry()}>
        Try again
      </Button>
    </div>
  );
}
