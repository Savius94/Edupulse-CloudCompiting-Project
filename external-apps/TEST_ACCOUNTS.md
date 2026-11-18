# Comptes de test (tenant démo `demo`)

Adresse établissement (à saisir dans Evalio / Notara) :

```text
http://host.docker.internal:8101
```

Mot de passe élèves & enseignants : `Demo123!`

## Élèves (emails EduPulse)

| Nom | Email | Classe | App conseillée |
|-----|-------|--------|----------------|
| Lucas Martin | `lucas.martin@demo.fr` | M1 Cloud & DevOps | Evalio |
| Chloé Dubois | `chloe.dubois@demo.fr` | M1 Cloud & DevOps | Evalio |
| Emma Bernard | `emma.bernard@demo.fr` | M1 Cloud & DevOps | Notara |
| Alexandre Mercier | `alexandre.mercier@demo.fr` | M1 Cloud & DevOps | Evalio |
| Hugo Petit | `hugo.petit@demo.fr` | M1 Cloud & DevOps | Notara |
| Inès Benali | `ines.benali@demo.fr` | M1 Cloud & DevOps | Evalio |
| Maxime Leroy | `maxime.leroy@demo.fr` | L3 Informatique | Notara |
| Thomas Roux | `thomas.roux@demo.fr` | L3 Informatique | Evalio |
| Léa Fontaine | `lea.fontaine@demo.fr` | L3 Informatique | Notara |
| Camille Girard | `camille.girard@demo.fr` | L3 Informatique | Evalio |
| Nathan Simon | `nathan.simon@demo.fr` | L3 Informatique | Notara |
| Sarah Lambert | `sarah.lambert@demo.fr` | L3 Informatique | Evalio |

## Enseignants

| Nom | Email |
|-----|-------|
| Pr. Sophie Laurent | `prof.laurent@demo.fr` |
| Dr. Marc Dupont | `prof.dupont@demo.fr` |
| Mme Claire Moreau | `prof.moreau@demo.fr` |

## Admin établissement

| Email | Mot de passe |
|-------|--------------|
| `direction@lycee-moulin.fr` | `AdminPass123!` |

## Peuplement automatique

```powershell
cd external-apps
python seed_demo_attempts.py
```

Le script soumet des QCM Evalio + Notara pour chaque élève avec des scores réalistes (pas tous à 20/20).
