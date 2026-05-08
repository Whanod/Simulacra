import { redirect } from "next/navigation";

export default async function ShareRunPage({
  params,
}: {
  params: Promise<{ runId: string }>;
}) {
  const { runId } = await params;
  redirect(`/results/${encodeURIComponent(runId)}?shared=1`);
}
