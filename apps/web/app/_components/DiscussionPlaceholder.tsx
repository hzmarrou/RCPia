export default function DiscussionPlaceholder() {
  return (
    <section className="discussion-placeholder" aria-labelledby="discussion-title">
      <span className="discussion-symbol" aria-hidden="true">···</span>
      <h2 id="discussion-title">Un espace pour la discussion</h2>
      <p>La discussion autour du PPS sera disponible ici. Aucun message n’est envoyé.</p>
      <p>Pour le moment, vous pouvez relire les comptes rendus et corriger les données du dossier.</p>
    </section>
  );
}
