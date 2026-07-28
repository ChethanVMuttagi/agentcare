import Link from "next/link";

export default function NotFound() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 py-24 text-center">
      <p className="text-lg font-semibold text-slate-900 dark:text-slate-100">Page not found</p>
      <p className="max-w-sm text-sm text-slate-500 dark:text-slate-400">
        The page you&rsquo;re looking for doesn&rsquo;t exist or you don&rsquo;t have access to it.
      </p>
      <Link
        href="/"
        className="inline-flex h-10 items-center justify-center rounded-md bg-slate-900 px-4 text-sm font-medium text-white hover:bg-slate-700 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-white"
      >
        Go home
      </Link>
    </div>
  );
}
