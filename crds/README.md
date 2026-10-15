# ARC CRD provenance

The four `actions.github.com_*.yaml` files are unmodified upstream CRDs from
[`gha-runner-scale-set-0.15.0`](https://github.com/actions/actions-runner-controller/tree/1945b42820760eb50893c8da77fdfc42cf8dad45/charts/gha-runner-scale-set-controller/crds),
commit `1945b42820760eb50893c8da77fdfc42cf8dad45`.

Fetched from `charts/gha-runner-scale-set-controller/crds/` under that tag.
No local ARC schema patches are applied.

```text
8851540fb515abb91c482753056ca0b1cc3c76b95b4b44ca64acc227d21759e7  actions.github.com_autoscalinglisteners.yaml
18ead86bc52efd0901757e9e56b07dd356ee24a058be0560b2e3db29a6402e33  actions.github.com_autoscalingrunnersets.yaml
440f23fe929fa7861e719ddf799ac0422b8f27eab1300ba7f3f32ff9d506d2d5  actions.github.com_ephemeralrunners.yaml
54980b21eb3c4b28f61f1c74b0add938cda9c77df4762fa0b318b7f1ea06f016  actions.github.com_ephemeralrunnersets.yaml
```

Apply using server-side apply to avoid oversized last-applied annotations. For
existing clusters, back up CRDs and ARC resources, review schema and field-owner
conflicts, and stop the old Controller before upgrading. See the root README for
the narrowly scoped, reviewed conflict-ownership procedure used in this PoC.
