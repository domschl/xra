# Maintainer: domschl <domschl@users.noreply.github.com>
_pkgname=xra
pkgname=$_pkgname
pkgver=0.1.1   # placeholder; real value comes from pkgver() (latest git tag)
pkgrel=1
pkgdesc="Archiving tool using rsync"
arch=('any')
url="https://github.com/domschl/xra"
license=('MIT')
depends=(
  'python'
  'python-rich'
  'rsync'
)
makedepends=(
  'git'
  'python-build'
  'python-installer'
  'python-wheel'
)
source=("$_pkgname::git+https://github.com/domschl/xra.git")
sha256sums=('SKIP')

pkgver() {
  cd "$_pkgname"
  git describe --tags --abbrev=0 | sed 's/^v//'
}

prepare() {
  cd "$_pkgname"
  # build exactly the latest release tag, not the branch tip
  _tag="v$(git describe --tags --abbrev=0 | sed 's/^v//')"
  git checkout --detach "$_tag"

  # guard: single source of truth — pyproject.toml version must match the tag
  local srcver
  srcver=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n1)
  if [ "$_tag" != "v$srcver" ]; then
    error "Latest tag $_tag does not match version '$srcver' in pyproject.toml"
    return 1
  fi
}

build() {
  cd "$_pkgname"
  python -m build --wheel --no-isolation
}

package() {
  cd "$_pkgname"
  python -m installer --destdir="$pkgdir" dist/*.whl
}
