import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Demander un accès — RCPia",
};

export default function RequestAccessPage() {
  return (
    <section className="panel">
      <h1>Demander un accès</h1>
      <p>
        RCPia est réservé aux professionnels de santé invités par un administrateur de leur
        établissement. Il n&apos;existe pas d&apos;inscription libre : aucun compte ne peut
        être créé sans invitation.
      </p>
      <p>
        Pour obtenir un accès, contactez l&apos;administrateur RCPia de votre service
        d&apos;urologie. Vous recevrez un lien d&apos;activation personnel, valable sept
        jours, qui vous permettra de choisir votre mot de passe.
      </p>
      <p className="muted">
        RCPia ne remplace pas une RCP. Toutes les propositions générées sont provisoires et
        doivent être validées par les experts humains.
      </p>
      <p>
        <Link href="/login">Retour à la connexion</Link>
      </p>
    </section>
  );
}
