# Local Model Hardware Preflight

Decision: **skip_local_model**

- CPU: x86_64 (4 logical CPUs)
- Physical RAM: 15.54 GiB
- Available RAM: 6.41 GiB
- Swap used: 1.26 GiB
- GPU/accelerator: False
- Free cache disk: 158.9 GiB
- Expected model/projector: 5.5/1.2 GB
- Expected peak memory: 10.0 GB

The official repository was not contacted because the hardware safety gate failed. No model was downloaded.

## Risks

- no supported GPU/accelerator detected
- available RAM is below the 1.25x expected-peak safety margin
- swap is already materially occupied
