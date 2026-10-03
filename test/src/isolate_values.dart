import 'dart:typed_data';

import 'package:fonix/fonix.dart';

/// A one-dimensional float32 isolate tensor holding [values].
OrtIsolateTensor float32IsolateTensor(List<double> values) =>
    OrtIsolateTensor.fromFloat32List(
      values: Float32List.fromList(values),
      shape: <int>[values.length],
    );
