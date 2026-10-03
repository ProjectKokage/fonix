/// Whether [value] has no unpaired surrogate, so it encodes to UTF-8 without
/// replacement.
bool hasWellFormedUtf16(String value) {
  for (var index = 0; index < value.length; index += 1) {
    final int unit = value.codeUnitAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      if (index + 1 >= value.length) {
        return false;
      }
      final int next = value.codeUnitAt(index + 1);
      if (next < 0xdc00 || next > 0xdfff) {
        return false;
      }
      index += 1;
    } else if (unit >= 0xdc00 && unit <= 0xdfff) {
      return false;
    }
  }
  return true;
}
