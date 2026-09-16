#!/usr/bin/env bash
# Fetch real, public STDF V4 sample files from open-source repositories into
# data/raw/stdf_samples/ (gitignored). URLs are pinned to commit SHAs; the
# sha256 of each file is recorded in data/raw/stdf_samples/MANIFEST.tsv and
# checked against the pinned expected value below (observed 2026-10-31).
#
# Usage: stdf/fetch_samples.sh            (from the repo root)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/data/raw/stdf_samples"
mkdir -p "$OUT"
GH=https://raw.githubusercontent.com

# name | url | expected sha256 | origin note
SAMPLES=(
"pystdf_lot2.stdf|$GH/cmars/pystdf/2215079c00e2f7b4c0be133baef4bf2a70d25859/data/lot2.stdf|e2a77df87fbf97c17e8e1a48bb4a702aa2307e1ce6abb41291022269af085958|pystdf demo data (GalaxySemi lot2)"
"pystdf_lot3.stdf|$GH/cmars/pystdf/2215079c00e2f7b4c0be133baef4bf2a70d25859/data/lot3.stdf|30ddd7ec4c351ded218d65147724c9e9a71731a1553cee7199c2ff01ced0caa0|pystdf demo data (GalaxySemi lot3)"
"stdfreader_a595.stdf|$GH/showjim/STDFReader/e5639bab63f20b11243dd4a43353c7390eef4834/sample_stdf/a595.stdf|1a835188e49010c78505e8cc1bacde1be22aa2e4c91be2898dba23808c68a94f|showjim/STDFReader sample"
"metis_test.std|$GH/Semi-ATE/Metis/a0077961e3c56f3a911a98a5430a40c727eddb6c/tests/test.std|41a2943489778acfb75f9d43c75fd136f7d923273f95d7975296c96227e6edfa|Semi-ATE Metis test file"
"stdf4j_test.stdf|$GH/liquidkarma/stdf4j/ebafe96f8df565b64699fbb5036b6aacad937d9c/testdata/test.stdf|ab942548430e0e052e38bad316292ecea70d00aa5d752d4554e2ff9dc4513409|stdf4j unit-test file"
"itest_main_lot_8ALX.stdf|$GH/itestinc/stdf2xls/5efa78c1568c32f96fd0d2a05948470f56db7d42/testing/main_Lot_8ALX06000.000.1_Wafer_7510057FSEE2_Dec_02_17h58m39s.stdf|6eaf878802a465e02e0151e6196ce0477226c22ce6373fdba6d9f21ab2601014|itestinc/stdf2xls wafer sort file"
"itest_LX7730.stdf|$GH/itestinc/stdf2xls/5efa78c1568c32f96fd0d2a05948470f56db7d42/testing/special_stdf/LX7730_T25_SN209_20160323090232.stdf|345d755944bfe707e939c7a3b4d21bc3131938e0240542519c57b28d46d51a8f|itestinc/stdf2xls 'special' file"
"itest_GX36_FT.stdf|$GH/itestinc/stdf2xls/5efa78c1568c32f96fd0d2a05948470f56db7d42/testing/special_stdf/TEST_FT_GX36_A2_FT_E_02032013_0230.stdf|ea5e48547101d93cf80c212c8d90046cd5b7882146af10ca87c752d539e50cce|itestinc/stdf2xls 'special' file"
"itest_bad_x17y8.stdf|$GH/itestinc/stdf2xls/5efa78c1568c32f96fd0d2a05948470f56db7d42/testing/bad_stdf/x17y8.stdf|61ec3093eb6ec63c628e67e51275e949dcb60a9f3a7a71dd6b1ab4946a214075|itestinc/stdf2xls 'bad_stdf' file"
"itest_bad_spreadsheet14.stdf|$GH/itestinc/stdf2xls/5efa78c1568c32f96fd0d2a05948470f56db7d42/testing/bad_stdf/spreadsheet14.stdf|f8f0c652a52dd78435d597faeebe2b3dc11ab0ebed3e5882e50afe66190b7392|itestinc/stdf2xls 'bad_stdf' file"
)

if command -v sha256sum >/dev/null; then SHA="sha256sum"; else SHA="shasum -a 256"; fi
MANIFEST="$OUT/MANIFEST.tsv"
printf "file\tbytes\tsha256\turl\tnote\n" > "$MANIFEST"
fail=0
for entry in "${SAMPLES[@]}"; do
  IFS='|' read -r name url want note <<<"$entry"
  dest="$OUT/$name"
  if [ ! -s "$dest" ]; then
    curl -fsSL --retry 3 -o "$dest.part" "$url"
    mv "$dest.part" "$dest"
  fi
  got=$($SHA "$dest" | awk '{print $1}')
  bytes=$(wc -c < "$dest" | tr -d ' ')
  if [[ "$want" != SHA_* && "$got" != "$want" ]]; then
    echo "sha256 mismatch for $name: got $got want $want" >&2; fail=1
  fi
  printf "%s\t%s\t%s\t%s\t%s\n" "$name" "$bytes" "$got" "$url" "$note" >> "$MANIFEST"
  echo "$name $bytes $got"
done
exit $fail
