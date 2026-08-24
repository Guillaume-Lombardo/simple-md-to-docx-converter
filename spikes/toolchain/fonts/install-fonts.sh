#!/usr/bin/env bash
set -euo pipefail

font_dir=/opt/md-converter/fonts
notice_dir=/usr/share/licenses/md-converter-fonts
work_dir=/tmp/md-converter-fonts
mkdir -p "$font_dir" "$notice_dir" "$work_dir"

fetch() {
    local url=$1 expected=$2 destination=$3
    curl --fail --location --silent --show-error "$url" --output "$destination"
    echo "$expected  $destination" | sha256sum --check --strict
}

liberation_archive="$work_dir/liberation.tar.gz"
fetch \
    https://github.com/liberationfonts/liberation-fonts/files/7261482/liberation-fonts-ttf-2.1.5.tar.gz \
    7191c669bf38899f73a2094ed00f7b800553364f90e2637010a69c0e268f25d0 \
    "$liberation_archive"
for face in \
    LiberationSans-Regular LiberationSans-Bold LiberationSans-Italic LiberationSans-BoldItalic \
    LiberationSerif-Regular LiberationSerif-Bold LiberationSerif-Italic LiberationSerif-BoldItalic \
    LiberationMono-Regular LiberationMono-Bold LiberationMono-Italic LiberationMono-BoldItalic
do
    tar --extract --gzip --file "$liberation_archive" --directory "$font_dir" \
        --strip-components=1 "liberation-fonts-ttf-2.1.5/${face}.ttf"
done
tar --extract --gzip --file "$liberation_archive" --to-stdout \
    liberation-fonts-ttf-2.1.5/LICENSE > "$notice_dir/Liberation-LICENSE"

dejavu_archive="$work_dir/dejavu.tar.bz2"
fetch \
    https://github.com/dejavu-fonts/dejavu-fonts/releases/download/version_2_37/dejavu-fonts-ttf-2.37.tar.bz2 \
    fa9ca4d13871dd122f61258a80d01751d603b4d3ee14095d65453b4e846e17d7 \
    "$dejavu_archive"
for face in \
    DejaVuSans DejaVuSans-Bold DejaVuSans-Oblique DejaVuSans-BoldOblique \
    DejaVuSerif DejaVuSerif-Bold DejaVuSerif-Italic DejaVuSerif-BoldItalic \
    DejaVuSansMono DejaVuSansMono-Bold DejaVuSansMono-Oblique DejaVuSansMono-BoldOblique
do
    tar --extract --bzip2 --file "$dejavu_archive" --directory "$font_dir" \
        --strip-components=2 "dejavu-fonts-ttf-2.37/ttf/${face}.ttf"
done
tar --extract --bzip2 --file "$dejavu_archive" --to-stdout \
    dejavu-fonts-ttf-2.37/LICENSE > "$notice_dir/DejaVu-LICENSE"

raw_font() {
    local repository=$1 commit=$2 family=$3 face=$4 expected=$5
    fetch "https://raw.githubusercontent.com/${repository}/${commit}/fonts/ttf/${family}-${face}.ttf" \
        "$expected" "$font_dir/${family}-${face}.ttf"
}

carlito_commit=3a810cab78ebd6e2e4eed42af9e8453c4f9b850a
raw_font googlefonts/carlito "$carlito_commit" Carlito Regular f6418f708baede9789daef5d458c0f53d2a888af9820e8062934e504fedc6595
raw_font googlefonts/carlito "$carlito_commit" Carlito Bold bb5d20f79b82599ec72983597437373a80f2d2085fa91fc144fd74e876a594db
raw_font googlefonts/carlito "$carlito_commit" Carlito Italic 0b019225e58d702bfedcbd35c21696769f8ee115cb6343f84c2f240312450d1c
raw_font googlefonts/carlito "$carlito_commit" Carlito BoldItalic b32928186c119599e03ca6a1ffc680fdcb7fac95772f4b95d989cf6cd3861517
fetch "https://raw.githubusercontent.com/googlefonts/carlito/${carlito_commit}/OFL.txt" \
    58402f82a7c332a700294988fe7554fbb0a63a8d27ccc1ee3bbc640311990a00 \
    "$notice_dir/Carlito-OFL.txt"

caladea_commit=336a529cfad3d103d6527752686f8331d13e820a
raw_font googlefonts/caladea "$caladea_commit" Caladea Regular f1e899278b7b4491aba5b6a8253c4b04c050cc59b21865be5c37559a775153cd
raw_font googlefonts/caladea "$caladea_commit" Caladea Bold ae3cb2dcbc925809dd29d2a44e9802211cab66be541bacbfc9c08c74b27c3742
raw_font googlefonts/caladea "$caladea_commit" Caladea Italic 4359a8e24f748b6447b1ff6d7a174febe70961d29f8bb8634b56dacd740a3deb
raw_font googlefonts/caladea "$caladea_commit" Caladea BoldItalic ccabaa7b7e2fdf253d2b1a5fa699dd8a3df8d835a9eb285ad82631a677eb76c0
fetch "https://raw.githubusercontent.com/googlefonts/caladea/${caladea_commit}/OFL.txt" \
    ccdab61d371d8c8683a128a92cd7d498dbdb1d37689f7cb21f1bf6b16658d213 \
    "$notice_dir/Caladea-OFL.txt"

find "$font_dir" -type f -name '*.ttf' -exec chmod 0444 {} +
chmod 0444 "$notice_dir"/*
