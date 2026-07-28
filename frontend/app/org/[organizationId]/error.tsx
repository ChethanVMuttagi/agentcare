"use client";

import { useEffect } from "react";

import { Button } from "@/components/ui/button";
import { describeError } from "@/lib/errors";

export default function OrgError({
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
    <div className="flex flex-col items-center justify-center gap-3 rounded-lg border border-red-200 bg-red-50 px-6 py-12 text-center dark:border-red-900/50 dark:bg-red-950/30">
      <p className="text-sm font-medium text-red-800 dark:text-red-300">Something went wrong</p>
      <p className="max-w-sm text-sm text-red-700 dark:text-red-400">{describeError(error)}</p>
      <Button variant="secondary" onClick={() => unstable_retry()}>
        Try again
      </Button>
    </div>
  );
}
