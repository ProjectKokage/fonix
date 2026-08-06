import 'dart:convert';

/// Validates JSON syntax, nesting, and unique object keys before `jsonDecode`.
///
/// Dart's decoder necessarily collapses duplicate object keys. Native protocol
/// payloads are trust boundaries, so every string-backed protocol calls this
/// guard before decoding. The function is package-internal and deliberately
/// omitted from `package:fonix/fonix.dart`.
void validateStrictJsonInternal(
  String source, {
  required String label,
  int maximumDepth = 32,
}) {
  if (maximumDepth < 1 || maximumDepth > 256) {
    throw RangeError.range(maximumDepth, 1, 256, 'maximumDepth');
  }
  _StrictJsonScanner(
    source,
    label: label,
    maximumDepth: maximumDepth,
  ).validate();
}

final class _StrictJsonScanner {
  _StrictJsonScanner(
    this.source, {
    required this.label,
    required this.maximumDepth,
  });

  final String source;
  final String label;
  final int maximumDepth;
  var _index = 0;

  void validate() {
    _skipWhitespace();
    _value(0);
    _skipWhitespace();
    if (_index != source.length) {
      _fail('contains trailing JSON content.');
    }
  }

  void _value(int depth) {
    if (depth > maximumDepth || _index >= source.length) {
      _fail('contains invalid or excessively nested JSON.');
    }
    switch (source.codeUnitAt(_index)) {
      case 0x7b:
        _object(depth + 1);
      case 0x5b:
        _array(depth + 1);
      case 0x22:
        _string();
      case 0x74:
        _literal('true');
      case 0x66:
        _literal('false');
      case 0x6e:
        _literal('null');
      default:
        _number();
    }
  }

  void _object(int depth) {
    _index += 1;
    _skipWhitespace();
    if (_consume(0x7d)) return;
    final Set<String> keys = <String>{};
    while (true) {
      if (_index >= source.length || source.codeUnitAt(_index) != 0x22) {
        _fail('contains an object key that is not a JSON string.');
      }
      final String key = _string();
      if (!keys.add(key)) {
        _fail('contains a duplicate object key.');
      }
      _skipWhitespace();
      if (!_consume(0x3a)) {
        _fail('contains an object key without a value.');
      }
      _skipWhitespace();
      _value(depth);
      _skipWhitespace();
      if (_consume(0x7d)) return;
      if (!_consume(0x2c)) {
        _fail('contains an object that is not comma separated.');
      }
      _skipWhitespace();
    }
  }

  void _array(int depth) {
    _index += 1;
    _skipWhitespace();
    if (_consume(0x5d)) return;
    while (true) {
      _value(depth);
      _skipWhitespace();
      if (_consume(0x5d)) return;
      if (!_consume(0x2c)) {
        _fail('contains an array that is not comma separated.');
      }
      _skipWhitespace();
    }
  }

  String _string() {
    final int start = _index;
    _index += 1;
    var escaped = false;
    while (_index < source.length) {
      final int codeUnit = source.codeUnitAt(_index);
      _index += 1;
      if (escaped) {
        if (codeUnit == 0x75) {
          for (var count = 0; count < 4; count += 1) {
            if (_index >= source.length || !_isHex(source.codeUnitAt(_index))) {
              _fail('contains an invalid JSON escape.');
            }
            _index += 1;
          }
        } else if (codeUnit != 0x22 &&
            codeUnit != 0x5c &&
            codeUnit != 0x2f &&
            codeUnit != 0x62 &&
            codeUnit != 0x66 &&
            codeUnit != 0x6e &&
            codeUnit != 0x72 &&
            codeUnit != 0x74) {
          _fail('contains an invalid JSON escape.');
        }
        escaped = false;
        continue;
      }
      if (codeUnit == 0x5c) {
        escaped = true;
      } else if (codeUnit == 0x22) {
        final Object? decoded;
        try {
          decoded = jsonDecode(source.substring(start, _index));
        } on FormatException {
          _fail('contains an invalid JSON string.');
        }
        if (decoded is! String) {
          _fail('contains an invalid JSON string.');
        }
        return decoded;
      } else if (codeUnit < 0x20) {
        _fail('contains a JSON control character.');
      }
    }
    _fail('contains an unterminated JSON string.');
  }

  void _literal(String literal) {
    if (_index + literal.length > source.length ||
        source.substring(_index, _index + literal.length) != literal) {
      _fail('contains an invalid JSON literal.');
    }
    _index += literal.length;
  }

  void _number() {
    final int start = _index;
    _consume(0x2d);
    if (_consume(0x30)) {
      if (_index < source.length && _isDigit(source.codeUnitAt(_index))) {
        _fail('contains an invalid JSON number.');
      }
    } else {
      if (_index >= source.length || !_isDigit19(source.codeUnitAt(_index))) {
        _fail('contains an invalid JSON value.');
      }
      while (_index < source.length && _isDigit(source.codeUnitAt(_index))) {
        _index += 1;
      }
    }
    if (_consume(0x2e)) {
      final int fractionStart = _index;
      while (_index < source.length && _isDigit(source.codeUnitAt(_index))) {
        _index += 1;
      }
      if (_index == fractionStart) {
        _fail('contains an invalid JSON number.');
      }
    }
    if (_consume(0x65) || _consume(0x45)) {
      _consume(0x2b) || _consume(0x2d);
      final int exponentStart = _index;
      while (_index < source.length && _isDigit(source.codeUnitAt(_index))) {
        _index += 1;
      }
      if (_index == exponentStart) {
        _fail('contains an invalid JSON number.');
      }
    }
    if (_index == start) {
      _fail('contains an invalid JSON value.');
    }
  }

  void _skipWhitespace() {
    while (_index < source.length) {
      final int codeUnit = source.codeUnitAt(_index);
      if (codeUnit != 0x20 &&
          codeUnit != 0x09 &&
          codeUnit != 0x0a &&
          codeUnit != 0x0d) {
        return;
      }
      _index += 1;
    }
  }

  bool _consume(int codeUnit) {
    if (_index < source.length && source.codeUnitAt(_index) == codeUnit) {
      _index += 1;
      return true;
    }
    return false;
  }

  Never _fail(String message) => throw FormatException('$label $message');

  static bool _isDigit(int codeUnit) => codeUnit >= 0x30 && codeUnit <= 0x39;

  static bool _isDigit19(int codeUnit) => codeUnit >= 0x31 && codeUnit <= 0x39;

  static bool _isHex(int codeUnit) =>
      _isDigit(codeUnit) ||
      (codeUnit >= 0x41 && codeUnit <= 0x46) ||
      (codeUnit >= 0x61 && codeUnit <= 0x66);
}
