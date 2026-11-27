# MELODI Supervisor Instance (`melodi_supervisor_instance`)

Agent sécurisé embarqué sur chaque instance **MELODI ERP centrale** (`type_distribution: "central"`) pour la connexion à un `melodi_supervisor_center`.

> Les serveurs de type `local` (POS / Edge) restent gérés par leur serveur central via `LocalServerService` et ne doivent pas utiliser ce module.

## Fonctionnement

1. **Installation manuelle** : placez le module dans `modules/melodi_supervisor_instance` et activez-le sur l'instance cible.
2. **Pairage OTP** : depuis `/admin/melodi_supervisor_instance/`, saisissez l'URL du `melodi_supervisor_center` (HTTPS obligatoire en production) et un jeton d'enrôlement à usage unique généré sur le Center.
3. **Verrouillage automatique (`app.supervision_mode = "managed"`)** : une fois pairée, l'instance verrouille automatiquement ses routes admin locales de mise à jour (modules, Core, langues, Hub, redémarrage → HTTP `403`).
4. **Convergence par Action Hooks de `base`** : l'agent ne duplique aucune logique d'installation. Il compare son inventaire au manifeste signé HMAC du Center et appelle les Action Hooks privilégiés de `base` (`base.supervisor_*`), puis déclenche `schedule_restart()` (repris par `scripts/start.py`) ou `soft_reload()`.
5. **Procédure d'urgence (Break-Glass)** : un compte super-administrateur (`is_sudo`) peut déconnecter l'instance depuis `/admin/melodi_supervisor_instance/` pour repasser immédiatement en mode `standalone`.

## Sécurité

- Chiffrement Fernet des identifiants locaux (`api_key`, `shared_secret`) en base de données.
- Signature **HMAC-SHA256 + Timestamp + Nonce** de toutes les requêtes sortantes (modèle Pull, aucun port entrant requis).
- Vérification de la signature HMAC du manifeste, de l'empreinte **SHA-256** et scan **anti-Zip-Slip** de chaque archive `.zip` avant exécution.
- Validation stricte **HTTPS** en environnement `prod`.
