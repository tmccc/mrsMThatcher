# Generated Identity Policy Production Activation Report

## 1. Executive result

GO. Audit integrity, canonical evidence, direct visual review, implementation tests, and full regression tests support production activation of generated identity policy only. Deployment details are finalised below.

## 2. Git and repository cleanup

Repository bookkeeping was already clean: HEAD `bd9613c` matched `origin/master`; `0715a21` remained the analysis-tooling commit; `b32498a` contained the counterfactual simulator; no tracked simulator modification remained. Unrelated historical artifacts stayed untracked and untouched.

## 3. Baseline commit state

Branch `master`, baseline HEAD `bd9613ce2015aac838d7677c63ee14151fad4881`. No staged changes existed before activation work.

## 4. Audit validation

Schema 1, analysis kind `generated_image_identity_dependence_audit`, 83 items. Coverage exactly matches 83 current approved generated images. Every image SHA-256 and filename-derived origin quote hash matches. Policy enums and all numeric fields validate and are finite.

Policy counts: 68 unrestricted, 9 small penalty, 0 strong penalty, 6 origin quote only.

## 5. Restricted images

Origin quote only:

- `tg_204869c6d40d2610b92dbcbff883303e72a7d1ae972b74d8c25d94b0ec61994d.png` - Margaret Thatcher; origin-specific women's-rights podium context.
- `tg_684fb4425801aebe8e8c52211921567da1cf4bd04cee430a3b30bed03d3276f5.png` - Solzhenitsyn/Stalin; high identity dependence, retention 5.
- `tg_912547decff638d587339e61584c8a4954e955087ca86c4fad6b8977507ed5c7.png` - Thatcher and origin-specific ladder metaphor; high dependence, retention 6.
- `tg_ae1ce8a812279c8dd97b71cfcd5efc578ba06dea161ef2de4a2091205eba279e.png` - Marx portrait; high dependence, retention 6.
- `tg_b09c4175a50f081e3807a29428f16f7644ef38c809f7f4ac8b20ea3c72aa3785.png` - Ronald Reagan tribute; high dependence, retention 6.
- `tg_faf99f3030693b0a55f0116194551792f3c4ea51261d7310eca7fb4d33b667d5.png` - Solzhenitsyn/Soviet scene; high dependence.

Small penalty:

- `tg_15bd818b86c205c6364eac5614bdda8a9daa968bc6336d9605cf05dacc6a7712.png`
- `tg_1a1d47475ade7da4e7e32a3dfef2e1aadb479df288d6a502a7d5f93f96e11500.png`
- `tg_2b351e489fc2b20f68c7f7867b3b17fb1636bb3ed2e7b6593e3345e10b09d5a2.png`
- `tg_56ad83fce3f67cb04eaa9bf4baaf858d776971e7e52f29e9c97f58a1b964e2f7.png`
- `tg_59b18840cbf67ac4a14a64f3858e7fcee40dfb483db36ec5b911fc22592dffaa.png`
- `tg_8312afd41d8a4871f7c62f21e6bab0840e48aef15d48e311fc152cc1e1d03e1d.png`
- `tg_a8afc3ebabfea07d2de701cc086c1119649217548f8e662cb09d062ea0bb5d54.png`
- `tg_f7e7033fc25ca1944294573e53a4eccc89a60eccb85bba232ba533b04f433a74.png`
- `tg_fb1906b50638e7e795309417cd776e02c2a0e97cf35b9526628fbb19b9429544.png`

These are recognisable Thatcher/Enoch Powell images whose generic political meaning remains useful; six points is a conservative preference rather than a ban.

## 6. Case-study review

Reviewed 20 origin-only interventions and 20 small-penalty interventions using paired production/replacement sheets in `review/generated_identity_production_activation/`. Origin-only cases consistently removed specific Marx, Solzhenitsyn, Reagan, or origin-specific Thatcher scenes from unrelated quotations. Replacements were generally generic Thatcher leadership imagery or coherent originals. No systematic poor generated-to-original replacement pattern appeared. The potentially strict women's-rights podium classification remained defensible because the scene contains explicit origin-specific symbolism and remains available on origin use.

Canonical calculations were independently verified: 5,000 indices; production/identity divergence 37.30%; 89 direct origin-only and 64 direct small-penalty displacements; no strong penalties; generated share 28.74% to 27.18%; entropy/top-10 concentration remained close; eight bounded forced resets and no fallback/exhaustion failure.

## 7. GO/NO-GO decision

GO. All required criteria passed. The policy targets a small audited subset, preserves origin use, did not collapse diversity, did not exhaust candidates, and improves the strongest reviewed cross-quote failures.

## 8. Production algorithm

Added distinct disabled-by-default `ENABLE_GENERATED_IDENTITY_POLICY_SCORING`. The existing pure categorical candidate-row helper is reused by shadow and production. Originals and origin matches retain baseline scores; unrestricted cross-quote candidates remain unchanged; small/strong penalties subtract 6/15; origin-only cross-quote candidates are removed from the real phase ranking. One existing `random.choice` resolves the actual policy tie.

## 9. Baseline comparator

When production policy is active, `GENERATED_IDENTITY_POLICY_APPLIED` replaces the redundant generated shadow result. The baseline is the highest unmodified score with deterministic basename tie handling, consuming no RNG. It records baseline and actual policy winners, actions, scores, counts, exclusions, source transition, tie count, and recovery phase. The old shadow event remains available when production policy is disabled.

## 10. Recovery behavior

An all-excluded phase raises `QuoteSpecificImageMismatch`; existing bounded alternate-quote, forced-cycle-reset, and last-image-fallback control flow continues. Excluded images are never silently restored. If every bounded attempt fails, posting fails safely without state mutation.

## 11. Digest changes

Added `## Generated identity policy`, explicitly describing real policy and observational baseline. The denominator is selections with any penalised/excluded cross-quote generated candidate. It reports direct displacements, transitions, excluded/penalised counts, replacements, phases, recovery, and changed baseline rows. Original editorial shadow rendering is unchanged.

## 12. Test results

- Dedicated production policy: `16 passed in 0.20s`.
- Identity production + existing identity/original shadow: `91 passed in 0.31s`.
- Selector/recovery/digest subset: `36 passed, 133 deselected in 8.64s`.
- Py-compile: PASS.
- `git diff --check`: PASS.
- Production-log isolation: PASS; no pytest, temporary path, dummy, loopback, or test-mode marker.

## 13. Full suite

`701 passed, 1 skipped, 1 warning in 377.25s`. Warning is the existing Starlette/httpx deprecation warning.

## 14. No large rerun

The 20 x 250 canonical simulation was not rerun. Existing validated records and direct visual samples were used.

## 15. Commit and push

Pending final deployment commit details.

## 16. Backup

Pending deployment backup details.

## 17. Config

Pending production config activation. Intended values: production policy true, audit current path, small 6.0, strong 15.0; original editorial shadow remains true with weight 0.32/cap 4.0.

## 18. Restart

Pending. Wrapper baseline PID 3631076; Python child baseline PID 3046050.

## 19. Startup

Pending.

## 20. Live observations

Pending.

## 21. Digest verification

Offline parser/render tests pass; live verification pending.

## 22. Final process state

Pending.

## 23. Final Git state

Pending.

## 24. Rollback instructions

Set `ENABLE_GENERATED_IDENTITY_POLICY_SCORING` false in ignored local config, preserve original editorial shadow and audit settings, validate JSON, identify the current Python child, send one SIGTERM to that child only, allow the unchanged wrapper to restart it, and verify the production-policy startup line is absent while original editorial shadow remains enabled. Restore source/config from the deployment backup only if flag disable does not recover operation.

## Safety confirmations

Original editorial production scoring was not enabled. Original editorial shadow is unchanged. No manual X, xAI, or external API call; no test post; no manual state or receipt edit; no log deletion/truncation/rotation; no wrapper signal; no force-push; and no large simulation rerun occurred during implementation and review.
