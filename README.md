# Annie the annotator

Infrastructure to store linguistic annotations.

# JVM-NLP

The jvm_nl_annotator relies on a server from this project:

https://github.com/ecohealthalliance/jvm-nlp

Why aren't we using a Swagger-generated client? It doesn't handle Maps very well.

https://github.com/wordnik/swagger-spec/issues/38

# TODO

* docs
* update and re-enable tests
* requirements.txt
* separate EHA-internal annotators from general annotators
* make token annotator raise errors if unexpected characters need to be consumed


## License
Copyright 2016 EcoHealth Alliance

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
