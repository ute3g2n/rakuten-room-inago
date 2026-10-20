# SC05/SC06 local-path redaction record

Task evidence included the local Windows profile path. To keep personally identifying workstation paths out of the repository diff, the worklog copies below were normalized. Exact original bytes were copied to the host temporary evidence archive before normalization; the original SHA-256 values are recorded here. The test nodes, result content and commands are otherwise unchanged. The archive is local-only and is not staged.

Archive root: `<host-temp>/inago-evidence-raw-20261020`

| File | Original SHA-256 | Normalized SHA-256 | Bytes (original → normalized) |
|---|---|---|---:|
| `worklogs/projects/maintenance/skill-chain-refactor-20261020/SC05/F3-followup/node-list.json` | `56ab05e8f5c5f3ca96b7d51fdb680e5472fcf96997cfb1e84fa10583024eedbe` | `f999459aea2d37bd94d267d6f57760e1f7aff086ee187ce0319af36d89d74d3c` | 9095 → 9041 |
| `worklogs/projects/maintenance/skill-chain-refactor-20261020/SC05/F3-followup/test.md` | `e2c92ec3df3298aa870960a086fa982e8f8880abccc4e5e1986a0801046d6554` | `e18f35729743652f449c996a060b8594287297065700e9bc64948b1855ea9362` | 4787 → 4760 |
| `worklogs/projects/maintenance/skill-chain-refactor-20261020/SC05/F4-followup/node-list.json` | `b4b9bb6cc33d801b88e2985ec07f409e2df901d29cfbb363639c1c83ec27f6a4` | `4ce5af802e91124ed016332b2e85e0872ac7828815a5df678d7f42470764182d` | 8973 → 8919 |
| `worklogs/projects/maintenance/skill-chain-refactor-20261020/SC05/F4-followup/test.md` | `fcf8ab3fe432edbeee3cacd5d6b9775182f47b8d0b003906f0455ffd9bda1cc8` | `6327ab474e8b7defad6074005dee9223d2039d327069511fb81009f153ad9827` | 3504 → 3477 |
| `worklogs/projects/maintenance/skill-chain-refactor-20261020/SC05/junit-rerun.xml` | `07fc84a9d1089d50e99a82c859e9a6da2db793d0db351e9eb1b2bced41fa3efe` | `570ac0f2a1784480176dd3e62b1b48cc7929e0a66109761d75328d7893c3e7e7` | 1915 → 1893 |
| `worklogs/projects/maintenance/skill-chain-refactor-20261020/SC05/junit-rerun2.xml` | `f0ad0ab5a85aacc2082dbaea872c139620dba2feffb88ff26b50faf995b357c5` | `5d5bee2da5f20f79beaeac7c985be585ed69167ade4ea3dfc3f8c7dbaad6aa56` | 3171 → 3149 |
| `worklogs/projects/maintenance/skill-chain-refactor-20261020/SC05/junit-rerun3.xml` | `291489108931b804c388d1d7b5072eaba5362f07dd06690222594d5a6ff9b698` | `44e79b78a5500b144594fa9b14b2287396b64a7be806ae80f1a05f04e92f1a32` | 2252 → 2230 |
| `worklogs/projects/maintenance/skill-chain-refactor-20261020/SC05/junit-rerun4.xml` | `76f7b53f10afa3ae2dc237cabf3d6c7baaff9d5bf5415ae1245af586fa39d9ac` | `f9735542d1015664c8e3322857a45eb811defc3f3df6e4fc5733ce3d7c7c3ab3` | 2804 → 2782 |
| `worklogs/projects/maintenance/skill-chain-refactor-20261020/SC05/junit-rerun5.xml` | `b4b67ecc0deac4b1b87269f1ab48b748f94f3e9f0b4b9d8b94f716e6b1412c23` | `db50c11bbee42d844e4e0f332b888588164bb60d95b5fd5c467898af7b49fab2` | 2006 → 1984 |
| `worklogs/projects/maintenance/skill-chain-refactor-20261020/SC05/junit-rerun6.xml` | `2ecec681e2f0e4fc988d23be2d7d858ccdb15ea999dabb0e320988dd57675432` | `cc52778fcd2ca8fcb7612276ec96dae07821eae57f1f5dc354bea1d68d54c4f1` | 2529 → 2507 |
| `worklogs/projects/maintenance/skill-chain-refactor-20261020/SC05/junit-rerun7.xml` | `ca792495b61874121d9a95406596e59f258f76e5a42dab0bf6e0af34caf4fc27` | `a7de1ef4c84bd50e3abdc7c2f728cca2ab18a14eee976c97e4e27d1542faabf2` | 3044 → 3022 |
| `worklogs/projects/maintenance/skill-chain-refactor-20261020/SC05/junit-rerun8.xml` | `3828b3c3bee2b4e84982a0b389adf9bed3841342085aacc788b7e281e24f2000` | `2ac980bfef709ec65d617271451dc0774f164952fc5e435ed5acfbb2ef83ed54` | 3044 → 3022 |
| `worklogs/projects/maintenance/skill-chain-refactor-20261020/SC05/junit-sc06-pre-fix.xml` | `55770a1e0ab5d6d1c91560ca9d561263bd5c1c8bb80a8caffc64844812c21308` | `255374cf8530afe63e9261e135a206fb4c264aea4dab22d8ceb8cdcaa2d61809` | 12962 → 12854 |
| `worklogs/projects/maintenance/skill-chain-refactor-20261020/SC05/junit-sc06-terminal-fix-initial.xml` | `f972dd2290ef6686f59ca7aa7bfa28eb8cd6153f4aa085736c29fdeab5cea6a3` | `2180b832a25042136643f3ded0fb17b4b85ada9c24c21d1ff9b1d9afa2b55ac8` | 13454 → 13346 |
| `worklogs/projects/maintenance/skill-chain-refactor-20261020/SC05/node-list.json` | `49f7e4ab2d10109d1b433d76c9bba75b0a1b3e7e4870f47113532163b2318774` | `a76447125ca86f69d1dbab06fc68d1afb84a9d60d50aff51a117d9c223068c05` | 9324 → 9216 |
| `worklogs/projects/maintenance/skill-chain-refactor-20261020/SC05/test.md` | `854d8d6642011443609414d13c7bddde8abbce520c39312f5f02d0070c547e8e` | `6b51cbd2dafa25351c800513d9c6a36c61a71a3e5470c56084a7ad5dd3f7685e` | 17572 → 17464 |
| `worklogs/projects/maintenance/skill-chain-refactor-20261020/SC06/follow-up-review.md` | `ed2cd12344f6c8292832a35dec5d42063bbd05db72bc522b0d60cdd5491e1d8e` | `5a6d790b43b06a6f7936b9b1ad0d88f68b10b52fb26da614a8727ae07dfe73e2` | 7483 → 7456 |
| `worklogs/projects/maintenance/skill-chain-refactor-20261020/SC06/independent-product-review.md` | `b09a23e375d6f18beb9e5be89bd816f96b984d18df655fca2ae995ece0c600f9` | `5f570b2bf082621a8b9525499df45fba87b983007a7c44ab6160d989687f7d0a` | 8575 → 8548 |

Normalization maps the user-specific temporary root to `%TEMP%`, the profile root to `%USERPROFILE%`, and pytest’s profile-specific temp directory label to `pytest-of-local-user`. Runtime command execution occurred under the corresponding host-local temporary path; the explicit pytest node list, options, basetemp suffix, start/end time, integer exit and outcome are retained in each follow-up record.


## F5 evidence copied and normalized

The F5 follow-up files were copied byte-for-byte to the same host-local temporary archive before redaction. Only local-profile path components were normalized in the repository copies; test names, assertions, times, exit code, and outcomes were retained.

| File | Original SHA-256 | Normalized SHA-256 | Bytes (original → normalized) |
|---|---|---|---:|
| `collect.log` | `23d7ed6b22aac88424128794eb64764d5eaa519a2b3bf86c9f25c63230436841` | `23d7ed6b22aac88424128794eb64764d5eaa519a2b3bf86c9f25c63230436841` | 2459 → 2459 |
| `junit.xml` | `cd80e379e107216d0a104c3fde4101796a5d790660d1e613524243cbab863026` | `cd80e379e107216d0a104c3fde4101796a5d790660d1e613524243cbab863026` | 4054 → 4054 |
| `node-list.json` | `c0a73f5a0a85e979ea1d708523ca028c1e336a84ffc8c1d602c17a65d15cbcfa` | `ec17dfc4fdab4c956fe1648d4663c29c221423bd852403b7a36d7effc1797e35` | 8951 → 8897 |
| `node-list.txt` | `e057ba0e352e786f1350fc406f7b6ec3039c9b5e1a3b990716605be4da91642e` | `e057ba0e352e786f1350fc406f7b6ec3039c9b5e1a3b990716605be4da91642e` | 2891 → 2891 |
| `test.log` | `a72296c27433491ca7d43edc1a619690f9b3e00562e802e42c0b1a5736cc6726` | `a72296c27433491ca7d43edc1a619690f9b3e00562e802e42c0b1a5736cc6726` | 534 → 534 |
| `test.md` | `dcaf5ac20076d6963df564b1ce4d4b9cac730eb1e788c18b5d7ccf35934cc043` | `0b1c474d770bc91f7c29610ea9c2da32e4fa9e898b2aab0479be57feed0071ed` | 3594 → 3567 |

Archive: `<host-temp>/inago-evidence-raw-20261020/SC05-F5-followup`.
