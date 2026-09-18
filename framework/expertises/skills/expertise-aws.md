<!-- EXPERTISE: expertise-aws — Adaptez à votre projet. -->
---
description: "Provisionner, modifier ou diagnostiquer une infrastructure AWS — IAM least-privilege, Terraform/CDK, state distant, garde-fous réseau et coûts. À utiliser dès qu'un projet référence des ressources AWS (provider `aws` en Terraform, stack CDK, `cloudformation.yaml`, `.aws/`, ARNs `arn:aws:...`) — pas pour Azure (`azurerm`) ni GCP (`google`), et pas pour du code applicatif sans lien avec le provisioning."
tools: ["read", "edit", "execute"]
---

# AWS (Amazon Web Services)

Expertise infra activée sur détection d'un provider `aws` (Terraform/CDK) ou d'artefacts AWS
(`.aws/`, ARNs, `cloudformation.yaml`) dans le projet. Le compte AWS est un domaine de blast-radius
à part entière : une erreur d'IAM ou de réseau y est immédiatement exploitable, pas seulement bogguée.

## Principes

- IAM least-privilege systématique : policies scopées à des actions et ressources précises, jamais
  `"Action": "*"` / `"Resource": "*"` sauf justification écrite et relue.
- Rôles IAM (assumés, avec `sts:AssumeRole`) plutôt que clés d'accès long-lived attachées à un
  utilisateur ; si une clé statique existe, elle doit avoir une rotation documentée.
- Aucune opération quotidienne sous le compte root — le root sert uniquement aux actions qui
  l'exigent explicitement (facturation, fermeture de compte), jamais au provisioning courant.
- Infra as code via Terraform (provider `aws`) ou AWS CDK — jamais de ressource créée à la main
  dans la console pour un environnement qui doit rester reproductible.
- State Terraform toujours distant (backend S3 versionné + verrou DynamoDB), jamais en local :
  un state local est un point de perte de données et de collision d'équipe.
- Tagging discipline sur toute ressource facturable (`Environment`, `Owner`, `CostCenter` au
  minimum) — sans tag, une ressource est invisible à l'allocation de coût et au nettoyage.
- CloudTrail et AWS Config actifs sur le compte : sans eux, un incident IAM ou réseau ne laisse
  aucune trace exploitable après coup.
- Security groups en moindre exposition : jamais `0.0.0.0/0` en entrée sauf sur les ports 80/443
  d'un load balancer public assumé comme tel.

## Garde-fou

Toute opération destructrice ou élargissant l'exposition — suppression d'un bucket S3 versionné ou
d'une base RDS sans snapshot final, désactivation de `deletion_protection`/MFA delete, ouverture
d'un security group ou d'une NACL à `0.0.0.0/0` sur autre chose que 80/443 d'un LB public — exige
l'affichage des ressources impactées et une confirmation explicite avant exécution.

## Provisionner ou modifier une ressource

Lire les fichiers `.tf`/CDK et le state existant → `aws sts get-caller-identity` pour confirmer
l'identité et le compte ciblés → `terraform plan -out=plan.tfplan` (ou `cdk diff` en CDK) → relire
le plan ligne à ligne, en particulier les `~ update in-place` sur IAM et réseau → appliquer
(`terraform apply plan.tfplan` / `cdk deploy`) → vérifier les tags de coût sur les ressources créées
→ `cc-verify.sh --stack aws` si le projet expose ce gate.

## Diagnostiquer un incident

Localiser le service et la fenêtre temporelle → `aws logs tail <log-group> --since <durée> --follow`
ou CloudWatch Logs Insights pour une requête structurée sur plusieurs groupes → corréler avec
CloudWatch Metrics/Alarms pour la charge et la latence → `aws cloudtrail lookup-events
--lookup-attributes AttributeKey=EventName,AttributeValue=<event>` pour retracer qui a fait quoi →
si le doute porte sur les permissions effectives, `aws iam simulate-principal-policy` avant de
modifier une policy à l'aveugle.

## Dérive de coût

Repérer le service en cause via Cost Explorer (regroupement par service puis par tag) → si le
suspect est du compute, croiser avec `aws ec2 describe-instances` pour des instances oubliées hors
auto-scaling → si le suspect est du stockage/logs, vérifier les politiques de cycle de vie S3 et la
rétention CloudWatch Logs → si le suspect est du data transfer, vérifier qu'aucun flux ne traverse
inutilement les zones de disponibilité ou les régions.

## Coûts et sécurité

- `aws iam simulate-principal-policy` avant d'élargir une policy — vérifier l'effet réel plutôt que
  de le déduire du JSON.
- Rétention CloudWatch Logs bornée explicitement (`retention_in_days`) — une rétention illimitée
  sur des logs verbeux est un poste de coût qui grossit silencieusement.
- Cost Explorer et AWS Budgets avec alertes sur les comptes/environnements actifs, pas seulement
  consultés a posteriori.
- Environnements éphémères (preview, sandbox, PoC) associés à une destruction planifiée
  (`terraform destroy` programmé ou TTL de stack) — l'oubli de destruction est la fuite de coût la
  plus fréquente.
- Buckets S3 : `Block Public Access` activé par défaut au niveau compte, exception explicite et
  justifiée si un bucket doit servir du contenu public.

## Checklist de revue

- Aucune policy IAM avec `Action: "*"` ou `Resource: "*"` non justifiée.
- State Terraform en backend distant (S3 + verrou DynamoDB), jamais local.
- Aucun security group/NACL ouvert à `0.0.0.0/0` hors 80/443 d'un LB public.
- Tags de coût (`Environment`, `Owner`, `CostCenter`) présents sur les ressources créées.
- CloudTrail et Config actifs sur le compte cible ; rétention des logs bornée.
