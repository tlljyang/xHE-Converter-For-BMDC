# native/xaacdec

AOSP **libxaac** 的 `xaacdec` 测试解码器构建（xHE-AAC / USAC 解码核心）。

- 上游: https://android.googlesource.com/platform/external/libxaac (Apache-2.0)
- 固定版本: `55de89d2d004016292d1fcb4b42904ad3f5b54a82` (v0.1.12)
- 本地参考构建 (Windows x64, MinGW 交叉编译) SHA-256:
  `de24fd5833ccdc10a310104329de15f0eba3f16c87891c43f079c1208bad5ef6`

## 构建

```bash
./build.sh                # macOS (clang) 或 Windows MSYS2/MinGW64
# 产物: out/bin/xaacdec[.exe]
```

CI 在三个矩阵平台各构建一份，随发行版捆绑（Onekey）或放入 `bin/`。
升级版本时改 `build.sh` 中的 `LIBXAAC_REF` 并更新本文件 SHA-256。
