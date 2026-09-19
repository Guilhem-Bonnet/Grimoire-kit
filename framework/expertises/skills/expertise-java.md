<!-- EXPERTISE: expertise-java — Adaptez à votre projet. -->
---
description: "Écrire ou faire évoluer du code Java — immuabilité par défaut, Optional en retour, streams lisibles, JUnit5/Mockito. À utiliser dès qu'une demande porte sur un fichier .java, un pom.xml ou un build.gradle — pas pour le C#/.NET ni pour l'infrastructure conteneurs/K8s."
tools: ["read", "edit", "execute"]
---

# Java

Expertise JVM activée sur détection d'un `pom.xml`/`build.gradle` ou d'un fichier `.java` : cette fiche couvre ce que le compilateur ne vérifie pas — immuabilité, gestion d'exceptions, reproductibilité du build.

## Principes

- Immuabilité par défaut : champs `final`, objets valeur immuables (constructeur + pas de setter, ou `record` en Java 16+) — la mutabilité partagée se justifie explicitement, elle ne se suppose pas.
- `Optional<T>` en type de retour pour signaler une absence légitime — jamais en champ de classe ni en paramètre de méthode (ni sérialisé), et jamais utilisé juste pour éviter un `null` check ponctuel.
- Streams pour les pipelines de transformation, pas au prix de la lisibilité — un stream de plus de 3-4 opérations chaînées avec effets de bord se réécrit en boucle explicite ou se découpe en méthodes nommées.
- Exceptions checked utilisées délibérément pour un échec récupérable par l'appelant (ex. `IOException` métier) — pas par réflexe sur toute méthode qui peut échouer ; les exceptions unchecked (`RuntimeException`) pour les erreurs de programmation.
- Reproductibilité du build : wrapper committé (`mvnw`/`gradlew` + `.mvn/wrapper` ou `gradle/wrapper`), versions de dépendances explicitement fixées, pas de plage ouverte (`[1.0,)`) en production.
- Analyse statique (SpotBugs/Checkstyle/PMD) dans le gate, pas une option — un warning ignoré porte une justification écrite, pas un silence.

## Garde-fou

Tout `catch` qui avale une exception (bloc vide ou seul `e.printStackTrace()`) ou tout état mutable partagé entre threads sans synchronisation (`volatile`, `synchronized`, structure concurrente) exige confirmation avant modification : ce sont les deux défauts qui ne se voient qu'en production, sous charge.

## Implémenter une feature

Lire le fichier cible et les interfaces qu'il implémente → identifier les types, DTOs et tests impactés → implémenter en respectant l'immuabilité par défaut et le contrat d'exceptions de la classe → écrire les tests JUnit5 (`@ParameterizedTest` pour les cas multiples) → `cc-verify.sh --stack java` (`mvn verify` ou `./gradlew check`).

## Corriger un bug

Écrire un test JUnit5 qui reproduit le bug → diagnostiquer la stack trace et vérifier qu'aucun `catch` intermédiaire n'a avalé l'exception d'origine → corriger au point exact → vérifier que le test du bug et la suite existante passent, analyse statique propre.

## Tests

`mvn verify` / `./gradlew check` pour le cycle complet (compilation + tests + analyse statique) → JUnit5 + Mockito pour isoler les dépendances externes → `mvn -q dependency:tree` (ou `./gradlew dependencies`) pour auditer la chaîne d'approvisionnement avant d'ajouter une dépendance transitive suspecte.

## Checklist de revue

- Aucun `catch` vide ou qui se contente d'un `printStackTrace()` sans relancer ni logger correctement.
- `Optional` seulement en type de retour, jamais en champ ou en paramètre.
- État mutable partagé entre threads protégé (`synchronized`, `java.util.concurrent`, immuabilité) ou absent.
- Le wrapper Maven/Gradle est committé et les versions de dépendances sont fixées, pas en plage ouverte.
- SpotBugs/Checkstyle passent sans suppression non justifiée.
