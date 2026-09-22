import { JOB_LABELS, type JobActivity } from "@/lib/activity";

export default function QueuedWorkNotice({
  jobs,
  reachable,
}: {
  jobs: JobActivity[];
  reachable: boolean;
}) {
  if (!reachable || jobs.length === 0 || jobs.some((job) => job.status !== "queued")) {
    return null;
  }
  const retry = jobs.some((job) => job.started_at !== null);
  const names = [...new Set(jobs.map((job) => JOB_LABELS[job.job_type] ?? "Traitement"))];
  return (
    <div className="poste-qualifier" role="status" aria-live="polite">
      <strong>{retry ? "En attente d’une nouvelle tentative" : "En attente de démarrage"}</strong>
      <p>
        {names.join(" / ")} : la demande est enregistrée, mais aucun de ces travaux
        n&apos;est actuellement en cours d&apos;exécution.
      </p>
      <p>
        Ce temps d&apos;attente n&apos;est pas une durée de traitement. Si l&apos;attente
        persiste, contactez le responsable de l&apos;application pour vérifier le service
        de traitement. Ne déposez pas le même document une seconde fois.
      </p>
    </div>
  );
}
