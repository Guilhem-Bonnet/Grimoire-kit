<!-- EXPERTISE: expertise-csharp — Adaptez à votre projet. -->
---
description: "Écrire ou faire évoluer du code C#/.NET — nullable reference types, async/await de bout en bout, IDisposable, xUnit. À utiliser dès qu'une demande porte sur un fichier .cs ou .csproj — pas pour le Java/JVM ni pour l'infrastructure conteneurs/K8s."
tools: ["read", "edit", "execute"]
---

# C#

Expertise .NET activée sur détection d'un `.csproj`/`.sln` ou d'un fichier `.cs` : le compilateur et les analyzers Roslyn portent une partie de la rigueur, cette fiche couvre le reste — nullabilité, async, cycle de vie des ressources.

## Principes

- Nullable reference types activé projet entier (`<Nullable>enable</Nullable>` dans chaque `.csproj`) — un `string?` non vérifié avant déréférencement est un défaut, pas un style.
- Async/await de bout en bout : jamais de `.Result` ou `.Wait()` sur une `Task` depuis du code synchrone (risque de deadlock sur un contexte de synchronisation ASP.NET/UI) ; `async void` interdit sauf handler d'événement — toute méthode async retourne `Task`/`Task<T>` pour rester awaitable et propager ses exceptions.
- `IDisposable`/`using` (ou `await using` pour `IAsyncDisposable`) systématique sur toute ressource non managée (flux, connexions, handles) — pas de `Dispose()` manuel oublié dans un chemin d'exception.
- LINQ pour la lisibilité des pipelines de transformation, mais matérialiser (`.ToList()`/`.ToArray()`) plutôt que réénumérer un `IEnumerable` coûteux, et éviter LINQ dans les boucles chaudes où l'allocation de closures/itérateurs pèse.
- Types immuables par défaut pour les données transportées (`record`, propriétés `init`) — la mutabilité se justifie, elle ne se suppose pas.
- `dotnet format --verify-no-changes` et les analyzers Roslyn (avec `.editorconfig` de règles) font partie du gate, pas une option.

## Garde-fou

Tout changement touchant l'état statique partagé (champs `static` mutables, singletons non thread-safe) ou une opération irréversible en base (migration EF Core avec `DROP`/perte de colonne) exige confirmation avant modification : ce sont les deux sources classiques de bugs invisibles en local et critiques en production.

## Implémenter une feature

Lire le fichier cible et les interfaces/contrats qu'il implémente → identifier les types, DTOs et tests impactés → implémenter avec nullable annotations complètes et async correctement propagé → écrire les tests xUnit (`[Theory]`/`[InlineData]` pour les cas multiples) → `cc-verify.sh --stack csharp` (`dotnet build`, `dotnet test`, `dotnet format --verify-no-changes`).

## Corriger un bug

Écrire un test xUnit qui reproduit le bug → diagnostiquer via la stack trace et, si deadlock suspecté, chercher un `.Result`/`.Wait()` sync-over-async dans la chaîne d'appel → corriger au point exact sans introduire de blocage synchrone → vérifier que le test du bug et la suite existante passent, `dotnet format` propre.

## Tests

`dotnet test` avec couverture (`coverlet` ou `dotnet-coverage collect`) pour situer les trous → xUnit + `Moq`/`NSubstitute` pour mocker les dépendances externes → tests d'intégration `WebApplicationFactory` pour les endpoints ASP.NET plutôt que des mocks profonds sur toute la pile HTTP.

## Checklist de revue

- Aucun `.Result`/`.Wait()` sur une `Task` en dehors d'un point d'entrée synchrone assumé et documenté.
- Aucun `async void` hors handler d'événement.
- Chaque ressource `IDisposable`/`IAsyncDisposable` est couverte par un `using`/`await using`, y compris sur les chemins d'exception.
- Pas de boxing silencieux dans un chemin chaud (structs passés en `object`, `ArrayList` non générique).
- `dotnet format --verify-no-changes` et les analyzers Roslyn passent sans `#pragma warning disable` non justifié.
