# Changelog

## [0.8.0](https://github.com/kaiketsu-labs/artifact-studio/compare/v0.7.1...v0.8.0) (2026-11-14)


### Features

* **collaboration:** let editors address an opted-in agent ([b6e1ce3](https://github.com/kaiketsu-labs/artifact-studio/commit/b6e1ce3fc9bfa569f153acc9b369f9d51d9cd43c))
* **offline:** edit saved slide text visually ([7ccded7](https://github.com/kaiketsu-labs/artifact-studio/commit/7ccded751de2f1162b35406dee71cdf2c84cb17d))
* **offline:** move rotate and remove saved slide objects ([5322efe](https://github.com/kaiketsu-labs/artifact-studio/commit/5322efea49683d67d05641d97045686309eee1b4))


### Bug Fixes

* **presenting:** focus slide navigation and show startup progress ([#200](https://github.com/kaiketsu-labs/artifact-studio/issues/200)) ([74639fe](https://github.com/kaiketsu-labs/artifact-studio/commit/74639fe10a6755777300c64a3a12cfcfb3184f89))
* **release:** retry a deferred nightly when CI passes ([786393c](https://github.com/kaiketsu-labs/artifact-studio/commit/786393c2e8b18c154653ec796b9330971550d440))

## [0.7.1](https://github.com/kaiketsu-labs/artifact-studio/compare/v0.7.0...v0.7.1) (2026-11-14)


### Bug Fixes

* **collaboration:** make agent routing and presence visible ([3d6a13d](https://github.com/kaiketsu-labs/artifact-studio/commit/3d6a13d69ad425f576548c445c3039cd4799b11b))
* **feedback:** hand screenshots off to a GitHub issue form ([d7ef9f9](https://github.com/kaiketsu-labs/artifact-studio/commit/d7ef9f973bdeb057d37da3a2da5fd7e98ddc95cd))
* **review:** recover stale and unconfirmed drafts ([e922376](https://github.com/kaiketsu-labs/artifact-studio/commit/e922376f6f05a3b20a43da2002613def07e7250c))

## [0.7.0](https://github.com/kaiketsu-labs/artifact-studio/compare/v0.6.0...v0.7.0) (2026-11-14)


### Features

* **chat:** address shared requests with an agent shortcut ([#162](https://github.com/kaiketsu-labs/artifact-studio/issues/162)) ([02ff3d4](https://github.com/kaiketsu-labs/artifact-studio/commit/02ff3d4230e1bd37459bebc28dc6dac46ccfcb2e))
* **collaboration:** show presence and improve hosted agent review ([#154](https://github.com/kaiketsu-labs/artifact-studio/issues/154)) ([df2f425](https://github.com/kaiketsu-labs/artifact-studio/commit/df2f425ffa94522161a8037ad0f02f8755c4ff15))
* **designs:** import and export reusable design packs ([#141](https://github.com/kaiketsu-labs/artifact-studio/issues/141)) ([403a7f7](https://github.com/kaiketsu-labs/artifact-studio/commit/403a7f78e8bbab3407186d886f591f108a585200))
* **hosting:** bring hosted collaboration and reliability fixes to main ([aaf2879](https://github.com/kaiketsu-labs/artifact-studio/commit/aaf28794117a902eb9dbdf5c708717582dff1887))
* **library:** add a compact artifact list view ([#155](https://github.com/kaiketsu-labs/artifact-studio/issues/155)) ([ccdc64b](https://github.com/kaiketsu-labs/artifact-studio/commit/ccdc64b7bf744179bd2de35f8ed45ed1e5cf32c1))
* simplify chat and add opt-in collaboration workflows ([#182](https://github.com/kaiketsu-labs/artifact-studio/issues/182)) ([6d84bc6](https://github.com/kaiketsu-labs/artifact-studio/commit/6d84bc647ddc1ed12f906956f0cff09a9705e2cc))
* **suggestions:** dismiss obsolete design sets ([#133](https://github.com/kaiketsu-labs/artifact-studio/issues/133)) ([35ea160](https://github.com/kaiketsu-labs/artifact-studio/commit/35ea160913ff2ce1c0da4a555d9659512ee247c8))


### Bug Fixes

* complete reviewed chat, hooks and recovery ([83795d1](https://github.com/kaiketsu-labs/artifact-studio/commit/83795d1faa9565061e97e7f07d8c53b5c1e89063))
* **deps:** bump astral-sh/uv from 0.13.7 to 0.13.8 ([#122](https://github.com/kaiketsu-labs/artifact-studio/issues/122)) ([3edc879](https://github.com/kaiketsu-labs/artifact-studio/commit/3edc87918fc9f338c1d49411e6835283e210adf4))
* **editor:** recover stalled dictation and show slide activity ([#148](https://github.com/kaiketsu-labs/artifact-studio/issues/148)) ([552f0a1](https://github.com/kaiketsu-labs/artifact-studio/commit/552f0a1499380305cabbc0983ae1aa06a6b71f16))
* **exports:** recover from stalled attachment preparation ([#125](https://github.com/kaiketsu-labs/artifact-studio/issues/125)) ([17eb6b6](https://github.com/kaiketsu-labs/artifact-studio/commit/17eb6b6d550180c3e2375ddc9dfd42fe70afe338))
* **feedback:** make the issue report dialog easier to use ([#150](https://github.com/kaiketsu-labs/artifact-studio/issues/150)) ([6fe665b](https://github.com/kaiketsu-labs/artifact-studio/commit/6fe665bd574024e43776ae76940543a4efb7334a))
* **hooks:** check assigned feedback after Claude edits ([#128](https://github.com/kaiketsu-labs/artifact-studio/issues/128)) ([3e16c44](https://github.com/kaiketsu-labs/artifact-studio/commit/3e16c44a36fb6f1a5962e08e21d085f167b955db))
* **library:** keep context menus open during refresh ([#144](https://github.com/kaiketsu-labs/artifact-studio/issues/144)) ([b85481e](https://github.com/kaiketsu-labs/artifact-studio/commit/b85481e849738c8e97c90ada5c1adbd735814213))
* **ui:** move issue reporting and restore favorite-slide previews ([#149](https://github.com/kaiketsu-labs/artifact-studio/issues/149)) ([ad065d8](https://github.com/kaiketsu-labs/artifact-studio/commit/ad065d81bc8b5b9f3cf1f7c624477d1848658ab4))
* **ui:** place server version in the sidebar ([#136](https://github.com/kaiketsu-labs/artifact-studio/issues/136)) ([7f100b8](https://github.com/kaiketsu-labs/artifact-studio/commit/7f100b8b7cc020844975a57ad4fe18931659792c))
* **ui:** remove the local workspace badge ([#142](https://github.com/kaiketsu-labs/artifact-studio/issues/142)) ([bbf007e](https://github.com/kaiketsu-labs/artifact-studio/commit/bbf007e2968bbed5d19c818c24e53ee61a82405b))


### Performance Improvements

* clarify editor actions and parallelize browser CI ([#183](https://github.com/kaiketsu-labs/artifact-studio/issues/183)) ([bc67f95](https://github.com/kaiketsu-labs/artifact-studio/commit/bc67f9563316dbc5e7a67bbcac41a8fc3d45dda2))
* **hosting:** skip unchanged shared deck snapshots ([#135](https://github.com/kaiketsu-labs/artifact-studio/issues/135)) ([128e06a](https://github.com/kaiketsu-labs/artifact-studio/commit/128e06a5638fd085ab0d625b9fd6d6cf873f7e58))

## [0.6.0](https://github.com/kaiketsu-labs/artifact-studio/compare/v0.5.0...v0.6.0) (2026-11-11)


### Features

* **sharing:** enable native imports for workspace owners ([51537fa](https://github.com/kaiketsu-labs/artifact-studio/commit/51537fa3d5e7e1db0ed0c208557fe0255c231031))
* **sharing:** enable native presentation creation for workspace owners ([e648164](https://github.com/kaiketsu-labs/artifact-studio/commit/e6481642a86654fedf51f1e0ab56ca52aacff49c))
* **sharing:** enable the native owner artifact timeline ([#115](https://github.com/kaiketsu-labs/artifact-studio/issues/115)) ([401c740](https://github.com/kaiketsu-labs/artifact-studio/commit/401c740bf1f604148c78a517cd3e09449825024a))
* **sharing:** enable the native private design library ([a889245](https://github.com/kaiketsu-labs/artifact-studio/commit/a8892454f56e124dca4cd75168bfce3845c4c6d2))
* **sharing:** enable the native scoped file export panel ([#116](https://github.com/kaiketsu-labs/artifact-studio/issues/116)) ([8a89831](https://github.com/kaiketsu-labs/artifact-studio/commit/8a89831cc1f5bed785494f67f4d63f25a83f41ee))
* **sharing:** open isolated workspaces in the native editor ([3943d13](https://github.com/kaiketsu-labs/artifact-studio/commit/3943d1304b4e6894051196630258c651a5e5adf0))
* **sharing:** organize hosted projects in the native library ([#113](https://github.com/kaiketsu-labs/artifact-studio/issues/113)) ([026c1ce](https://github.com/kaiketsu-labs/artifact-studio/commit/026c1cedb9e0837000d35540088745b25818a086))
* **sharing:** provision personal workspaces for verified accounts ([cd31c49](https://github.com/kaiketsu-labs/artifact-studio/commit/cd31c49c3c7e03f66658c9aa092e6b48e1d6e065))
* **sharing:** restore private favorites in the native editor ([#114](https://github.com/kaiketsu-labs/artifact-studio/issues/114)) ([bc1db0b](https://github.com/kaiketsu-labs/artifact-studio/commit/bc1db0bbdd395cb91db07293eb6cc7a14d52f973))
* **sharing:** show scoped covers in the native library ([ea885fd](https://github.com/kaiketsu-labs/artifact-studio/commit/ea885fdcb546d1d6a817387930ed9c5237ce8ec9))
* **sharing:** support attachments in shared conversations ([a2578c4](https://github.com/kaiketsu-labs/artifact-studio/commit/a2578c4501ca479327f7fc3e84735ca57ae5b8df))
* **sharing:** use the native editor for scoped collaborators ([0859100](https://github.com/kaiketsu-labs/artifact-studio/commit/08591003aa6d0e89748acab2906e1ab1a7037e43))
* **slides:** simplify menus and add export options ([712e0fa](https://github.com/kaiketsu-labs/artifact-studio/commit/712e0fa2818d2cdaee6c0d0a4d12ee216ec391fc))


### Bug Fixes

* **deps:** bump the python-compatible group with 2 updates ([#118](https://github.com/kaiketsu-labs/artifact-studio/issues/118)) ([7b09f10](https://github.com/kaiketsu-labs/artifact-studio/commit/7b09f10dbecd8fbc18c24c7182c801a2a50f981f))
* **hosted:** separate workspace status from server checks ([#119](https://github.com/kaiketsu-labs/artifact-studio/issues/119)) ([df93a46](https://github.com/kaiketsu-labs/artifact-studio/commit/df93a46ab7e80964d9b435f23c028de81db383c7))
* **recovery:** exclude design examples from artifact counts ([#117](https://github.com/kaiketsu-labs/artifact-studio/issues/117)) ([c4f3f89](https://github.com/kaiketsu-labs/artifact-studio/commit/c4f3f8971f7c21cc8d5eb0537b3d2cff77e9822b))
* **sharing:** allow editors to approve shared artifact versions ([cd5d944](https://github.com/kaiketsu-labs/artifact-studio/commit/cd5d944ed28a31d519752f4b30ea1f6954acd7b4))
* **sharing:** keep concurrent editor asset loads available ([0df1273](https://github.com/kaiketsu-labs/artifact-studio/commit/0df1273e56de6017513239beaca62082002d4fa7))
* **sharing:** keep library shortcuts in the selected workspace ([a5ef0db](https://github.com/kaiketsu-labs/artifact-studio/commit/a5ef0dbdd14d0955ffeca119911cd350572c8609))
* **sharing:** keep update markers per reader ([6fa6b74](https://github.com/kaiketsu-labs/artifact-studio/commit/6fa6b7403c351a7c5b2f9096fd55f91dc1e04b54))


### Documentation

* **hosting:** explain remote Studio architecture and sharing ([#101](https://github.com/kaiketsu-labs/artifact-studio/issues/101)) ([d3dbbd3](https://github.com/kaiketsu-labs/artifact-studio/commit/d3dbbd35160cb9a5b7c209d97fd39fd11999fb88))

## [0.5.0](https://github.com/kaiketsu-labs/artifact-studio/compare/v0.3.2...v0.5.0) (2026-11-10)

Version 0.4.0 was superseded before publication. This release includes the changes since 0.3.2.

### Features

* **Multiplayer:** share artifacts, projects or entire workspaces through an authenticated tunnel. Collaborators use their own scoped agents, Shared and My agent chats, invitations and revocable access. Additional workspaces are isolated. ([#84](https://github.com/kaiketsu-labs/artifact-studio/pull/84))
* **Docker:** run the server with persistent workspace storage and sandboxed rendering through Docker Compose. ([#70](https://github.com/kaiketsu-labs/artifact-studio/pull/70))
* **Chat:** open saved document previews directly from reference pills. ([#71](https://github.com/kaiketsu-labs/artifact-studio/pull/71))
* **Maintenance:** automate audited dependency updates and verify stable-release publication against successful CI for each release commit. ([#72](https://github.com/kaiketsu-labs/artifact-studio/pull/72), [#88](https://github.com/kaiketsu-labs/artifact-studio/pull/88))

### Bug fixes

* Preserve open reference previews during composer refreshes. ([#95](https://github.com/kaiketsu-labs/artifact-studio/pull/95))
* Keep Version history unavailable until a slide is ready, including during presentation switches and in empty workspaces. Stabilize parallel browser-test server startup. ([#84](https://github.com/kaiketsu-labs/artifact-studio/pull/84))
* Clarify chat toolbar, favorite-library and full-presentation icons. ([#85](https://github.com/kaiketsu-labs/artifact-studio/pull/85), [#86](https://github.com/kaiketsu-labs/artifact-studio/pull/86), [#87](https://github.com/kaiketsu-labs/artifact-studio/pull/87), [#94](https://github.com/kaiketsu-labs/artifact-studio/pull/94))
* Update locked Python, release-tooling and container dependencies, including transitive dependency fixes. ([#89](https://github.com/kaiketsu-labs/artifact-studio/pull/89), [#91](https://github.com/kaiketsu-labs/artifact-studio/pull/91), [#93](https://github.com/kaiketsu-labs/artifact-studio/pull/93))

### Setup

* Lead Mac installation with the menu-bar app. ([#92](https://github.com/kaiketsu-labs/artifact-studio/pull/92))
* Local mode remains the default. Multiplayer requires its separate server entrypoint, authenticated tunnel and agent connections; follow the [multiplayer guide](https://github.com/kaiketsu-labs/artifact-studio/blob/v0.5.0/docs/MULTIPLAYER.md).

## [0.3.2](https://github.com/kaiketsu-labs/artifact-studio/compare/v0.3.1...v0.3.2) (2026-11-06)


### Bug Fixes

* **chat:** preview saved references when hovering title pills ([#69](https://github.com/kaiketsu-labs/artifact-studio/issues/69)) ([d56e36c](https://github.com/kaiketsu-labs/artifact-studio/commit/d56e36c09b3a438ae1faa82f98b00ddea82ea977))
* restore thumbnail recovery, portable backups and compact references ([#67](https://github.com/kaiketsu-labs/artifact-studio/issues/67)) ([312ac55](https://github.com/kaiketsu-labs/artifact-studio/commit/312ac55fef73bb64ae103295ec84228dd63d7c68))

## [0.3.1](https://github.com/kaiketsu-labs/artifact-studio/compare/v0.3.0...v0.3.1) (2026-11-06)


### Bug Fixes

* **artifacts:** preserve canonical snapshot inventory order ([10419e3](https://github.com/kaiketsu-labs/artifact-studio/commit/10419e306ec324d8338f3325d31a4d09e7ce41d8))

## [0.3.0](https://github.com/kaiketsu-labs/artifact-studio/compare/v0.2.0...v0.3.0) (2026-11-06)


### Features

* add chat path references with cursor previews ([#61](https://github.com/kaiketsu-labs/artifact-studio/issues/61)) ([e1801e7](https://github.com/kaiketsu-labs/artifact-studio/commit/e1801e70ab6f234590406f889948631baf23b366))
* add favorites and cross-presentation chat references ([#57](https://github.com/kaiketsu-labs/artifact-studio/issues/57)) ([598b18a](https://github.com/kaiketsu-labs/artifact-studio/commit/598b18a4baf8a6229b5ade27d8804789a0528d09))
* add opt-in nightly releases and conditional update actions ([#63](https://github.com/kaiketsu-labs/artifact-studio/issues/63)) ([d12bc04](https://github.com/kaiketsu-labs/artifact-studio/commit/d12bc041b7cd428a7071d8ce2388284628ee95e7))
* **app:** show installed versions and stable release update indicators ([d249213](https://github.com/kaiketsu-labs/artifact-studio/commit/d24921310e30547f7981aab069b918e438fba3b5))
* **designs:** choose the workspace default in the library ([73aa0ae](https://github.com/kaiketsu-labs/artifact-studio/commit/73aa0ae5ef9ad66d795390ad017726219e72a68b))
* **designs:** choose the workspace default in the library ([#59](https://github.com/kaiketsu-labs/artifact-studio/issues/59)) ([21269d5](https://github.com/kaiketsu-labs/artifact-studio/commit/21269d523b0b529a169af7bb45761a021ef816e4))


### Bug Fixes

* **backup:** prevent stale workspace snapshot overwrites ([6347905](https://github.com/kaiketsu-labs/artifact-studio/commit/6347905c5db4d2b87b5448fdb430ef47fac6a0e6))
* **backup:** release database writes before archive compression ([91d5107](https://github.com/kaiketsu-labs/artifact-studio/commit/91d51071897713d732bfc2f61e9e39b3dd23ccae))
* **backup:** release database writes before archive compression ([#64](https://github.com/kaiketsu-labs/artifact-studio/issues/64)) ([44da804](https://github.com/kaiketsu-labs/artifact-studio/commit/44da804133a4b1377df6d1fff3425807c73a1239))
* **macos:** bound diagnostic logs and add safe cleanup ([#65](https://github.com/kaiketsu-labs/artifact-studio/issues/65)) ([d65791d](https://github.com/kaiketsu-labs/artifact-studio/commit/d65791d761b701013adef6facc2c65eef13b2d34))
* persist library thumbnails and bound background rendering ([#60](https://github.com/kaiketsu-labs/artifact-studio/issues/60)) ([3417fad](https://github.com/kaiketsu-labs/artifact-studio/commit/3417fadc8a3e20571c89a68a8adf6e7a0a74dca5))
* **ui:** keep update actions beside the installed version ([1494bf9](https://github.com/kaiketsu-labs/artifact-studio/commit/1494bf95743a38ae7b61b6b28aa759e5cff1b572))


### Performance Improvements

* reduce retained previews and repeated workspace work ([#62](https://github.com/kaiketsu-labs/artifact-studio/issues/62)) ([80eed27](https://github.com/kaiketsu-labs/artifact-studio/commit/80eed27aad814e586db93826350b3c2e9b550c76))


### Documentation

* explain verified multi-computer backup conflicts ([af536c0](https://github.com/kaiketsu-labs/artifact-studio/commit/af536c08e4205aa37f3e289bef491779e4092fda))

## [0.2.0](https://github.com/kaiketsu-labs/artifact-studio/compare/v0.1.0...v0.2.0) (2026-11-05)


### Features

* **chat:** preview image attachments and speed up artifact thumbnails ([1bb44d6](https://github.com/kaiketsu-labs/artifact-studio/commit/1bb44d607e94f6d39d651714c9233875561d7695))
* **editor:** streamline alternatives and clarify version history ([#47](https://github.com/kaiketsu-labs/artifact-studio/issues/47)) ([454e75d](https://github.com/kaiketsu-labs/artifact-studio/commit/454e75d43c0728edbd5aa6ea117b328da30221f4))
* **review:** wake connected agents for every artifact type ([76566c8](https://github.com/kaiketsu-labs/artifact-studio/commit/76566c80527fa193e08f4ccc07b2d85643eab0ff))


### Bug Fixes

* **notes:** prevent editing before the saved slide notes have loaded


### Documentation

* **readme:** reuse pixel presentation visuals ([671ebd7](https://github.com/kaiketsu-labs/artifact-studio/commit/671ebd7d4d9ea7581a2fa01b6d72091e1c02bfec))

## 0.1.0 (2026-11-04)


### ⚠ BREAKING CHANGES

* **agent:** reduce repeated context and idle review work
* **brand:** rename the workspace to artifact studio
* **mcp:** reduce repeated agent context and mutation payloads

### Features

* add headline, text and graphic alternatives with live previews ([c086c28](https://github.com/kaiketsu-labs/artifact-studio/commit/c086c28ac0f93a1ded42e8b34ab081a0249cbece))
* add resizable chat and Markdown messages ([#2](https://github.com/kaiketsu-labs/artifact-studio/issues/2)) ([90cfe3b](https://github.com/kaiketsu-labs/artifact-studio/commit/90cfe3bb602d15c62ae2e8d950593e4380039937))
* **agents:** apply reviewed patch files through stateless mcp ([8fc9542](https://github.com/kaiketsu-labs/artifact-studio/commit/8fc9542c7707c3214f198d0dd1291d117d04814b))
* **authoring:** support custom interactive html slides ([29cff7d](https://github.com/kaiketsu-labs/artifact-studio/commit/29cff7dea83aaa4d774d3c5f2abe2a60f969096f))
* autosave notes and clarify storage and backup ([#19](https://github.com/kaiketsu-labs/artifact-studio/issues/19)) ([7c9b7f6](https://github.com/kaiketsu-labs/artifact-studio/commit/7c9b7f6100ed2706ae6584669aad0de62c851d3e))
* **copilot:** add scoped microsoft 365 integration ([f491acd](https://github.com/kaiketsu-labs/artifact-studio/commit/f491acd14c906b0673babc6dfc9a379696d74608))
* **designs:** edit text references in a local code editor ([a8a0a5a](https://github.com/kaiketsu-labs/artifact-studio/commit/a8a0a5a3e3341593ea6ed1ba3343d8f3bb4f5470))
* **designs:** preview images and powerpoint covers in file context ([2b2fd53](https://github.com/kaiketsu-labs/artifact-studio/commit/2b2fd53bbffcf99ebd7abbb9131af167ee9ab2e7))
* **editor:** add marquee selection and image exports ([61edba5](https://github.com/kaiketsu-labs/artifact-studio/commit/61edba5bf823c387c3df39040d9257d9abbeda83))
* **editor:** refine layout and responsive panel navigation ([f636d2b](https://github.com/kaiketsu-labs/artifact-studio/commit/f636d2b601efca86e982ff9d52eae45da7a32d0d))
* **editor:** simplify text formatting for everyday use ([3c58750](https://github.com/kaiketsu-labs/artifact-studio/commit/3c58750938cd73d1f972ef53e9b7fbf703bd9ff6))
* **history:** show saved slide thumbnails in the version timeline ([4b2231a](https://github.com/kaiketsu-labs/artifact-studio/commit/4b2231ac9fbc7edf569cecac8c7334b8796221a2))
* **hooks:** install user-level hooks with --global ([60e8f3d](https://github.com/kaiketsu-labs/artifact-studio/commit/60e8f3d5d59a740747995e81ff1f7af303ed6ef4))
* **hub:** add projects and artifact review ([fadd70c](https://github.com/kaiketsu-labs/artifact-studio/commit/fadd70ca089edcfef80d7cf2eec724db67a4b52a))
* **learning:** bundle weekly transcript preference review skill ([1fa43a9](https://github.com/kaiketsu-labs/artifact-studio/commit/1fa43a9b9b7bd85bf5271a31b7269c202811801d))
* **library:** add artifact sorting and file-type groups ([8142eec](https://github.com/kaiketsu-labs/artifact-studio/commit/8142eec7f63345ab4304b985e1fe1ef88488ce89))
* **macos:** add guarded updates and grouped settings ([145a9a1](https://github.com/kaiketsu-labs/artifact-studio/commit/145a9a1e6cacf63b586c33e3d40c7a0f7dce9755))
* **macos:** add menu-bar controller and isolate parallel drafts ([c215088](https://github.com/kaiketsu-labs/artifact-studio/commit/c215088da78c3403cd6a32d2f5ac13c98962b080))
* **mcp:** reduce repeated agent context and mutation payloads ([895f8e7](https://github.com/kaiketsu-labs/artifact-studio/commit/895f8e79171ebdcdcde8e7f7aa68c0ceca95b25e))
* **navigation:** add connected project shortcuts and recents ([8499bb7](https://github.com/kaiketsu-labs/artifact-studio/commit/8499bb797da0da088b283b4989968693003c613b))
* **preview:** replay slide animations from the editor ([6b69344](https://github.com/kaiketsu-labs/artifact-studio/commit/6b69344c8bb3c5f08a117542b080f5dda07a406d))
* **projects:** organize projects with nested folders ([55baa87](https://github.com/kaiketsu-labs/artifact-studio/commit/55baa87e3d73106eca8e26e68a94897b05f4e15a))
* **review:** auto-update previews and select multiple slide objects ([6f9d0fa](https://github.com/kaiketsu-labs/artifact-studio/commit/6f9d0faca48acf8efe0fd6bdfa3995d5397221d1))
* **review:** render slide images and reveal states through mcp ([ab5d5a2](https://github.com/kaiketsu-labs/artifact-studio/commit/ab5d5a2a1519410e03ff7a60df229afa117b86d6))
* **review:** show connected harness and session in live control ([eb093c2](https://github.com/kaiketsu-labs/artifact-studio/commit/eb093c27cc30e4f74b0b8387c43b6ed6241527ce))
* **rules:** save editable preferences with workspace sync ([5006f46](https://github.com/kaiketsu-labs/artifact-studio/commit/5006f46efd6c1edbaa3c873deab0d109ee5f4e30))
* **setup:** add guided onboarding commands ([a49fc40](https://github.com/kaiketsu-labs/artifact-studio/commit/a49fc400f0a0cb59d322eb9a9d12f8128da9b779))
* **sharing:** add outlook attachments and focus viewing ([42405dc](https://github.com/kaiketsu-labs/artifact-studio/commit/42405dc7d7af6179b3db5165c8fea614e4efc0e4))
* **slides:** add powerpoint-style hidden slides ([97f7c23](https://github.com/kaiketsu-labs/artifact-studio/commit/97f7c23b31d794255cff0508937eb8612955059d))
* **storage:** explain repository actions with tooltips ([#42](https://github.com/kaiketsu-labs/artifact-studio/issues/42)) ([507ded2](https://github.com/kaiketsu-labs/artifact-studio/commit/507ded296a7f51c46bd36423aa5f0cda5b6b2d9d))
* **storage:** show the last local save time ([d6a0cb5](https://github.com/kaiketsu-labs/artifact-studio/commit/d6a0cb55e08dcad521896d81dee37387897b0cab))
* **studio:** add automatic sync and animation review guidance ([2dd4c81](https://github.com/kaiketsu-labs/artifact-studio/commit/2dd4c813f34aa01e3918b0f5dad664f7f1688f9c))
* **studio:** add live review and visual documentation ([8625ae0](https://github.com/kaiketsu-labs/artifact-studio/commit/8625ae091d849a9b9ea58547ac8e68b254308c38))
* **studio:** add precise edit suggestions and reusable chart builds ([72c4495](https://github.com/kaiketsu-labs/artifact-studio/commit/72c449560595006dfc7ee6a83b85d4f1204eb3a4))
* **studio:** add slide overview and familiar chat controls ([332bd24](https://github.com/kaiketsu-labs/artifact-studio/commit/332bd2433348ffa0b5d2c9f35b072a89ae01a4e0))
* **workspace:** add agent slide review and private reference sync ([5925e2e](https://github.com/kaiketsu-labs/artifact-studio/commit/5925e2e2b24f4ac66e30c21a6b9472bcb3218610))
* **workspace:** add contextual menus and refresh agent guidance ([bac69d6](https://github.com/kaiketsu-labs/artifact-studio/commit/bac69d681958a1c74f4e146e3caa3c81980acadd))
* **workspace:** add dark mode and fix fullscreen shortcut ([c9d87f0](https://github.com/kaiketsu-labs/artifact-studio/commit/c9d87f0355d4d5f50e6c1e90f35c792d97123dcc))
* **workspace:** add recoverable slide controls and object selection ([210bde2](https://github.com/kaiketsu-labs/artifact-studio/commit/210bde2ac23208c88a30a51d9a2201844ec93a8b))
* **workspace:** add recovery archives and visual version history ([38fbc04](https://github.com/kaiketsu-labs/artifact-studio/commit/38fbc04c8adb4921378a37afed2fbe5eeacd2e3c))
* **workspace:** add slide chats and persistent authoring rules ([a1524aa](https://github.com/kaiketsu-labs/artifact-studio/commit/a1524aadc4347018d8f6dce8b252c273abee19c3))
* **workspace:** add slide reordering and portable project exports ([29b88bf](https://github.com/kaiketsu-labs/artifact-studio/commit/29b88bffc85deb7d9e945acd1690e1e71d1e1714))
* **workspace:** group actions in accessible menu with shortcuts ([70c7745](https://github.com/kaiketsu-labs/artifact-studio/commit/70c7745882d9397c9b7cf2a5e17d05b6a2e3a892))
* **workspace:** improve artifact editing, focused viewing and backup controls ([c6cd301](https://github.com/kaiketsu-labs/artifact-studio/commit/c6cd3016ee4f7f028deb0f88697f64bb09321444))
* **workspace:** preview text references and attach agents automatically ([1c99778](https://github.com/kaiketsu-labs/artifact-studio/commit/1c99778f0057fbde5778589e7b21673a9265c3c5))
* **workspace:** redesign project picker and add creation action ([adcefce](https://github.com/kaiketsu-labs/artifact-studio/commit/adcefcec51bdc2049c90e1126d38e4f291ab93b9))


### Bug Fixes

* **chat:** send messages with enter ([8381e68](https://github.com/kaiketsu-labs/artifact-studio/commit/8381e680cc911c281d1902fbd72cddd1d6407782))
* **editor:** anchor panel toggles at opposite toolbar edges ([a33b7c6](https://github.com/kaiketsu-labs/artifact-studio/commit/a33b7c69aeaddb025704e3597e7c6f463c9abe43))
* **editor:** float compact formatting controls without shifting content ([81c6e4d](https://github.com/kaiketsu-labs/artifact-studio/commit/81c6e4db9381c7de61b4df7d24c0ce198163857f))
* **exports:** scale slide typography and document keyboard shortcuts ([148cb55](https://github.com/kaiketsu-labs/artifact-studio/commit/148cb55ec3572c8f6bb8dfee482c18faa66902f4))
* **history:** collapse consecutive unchanged content in the timeline ([0447226](https://github.com/kaiketsu-labs/artifact-studio/commit/0447226919ada08ff67bc4488f621926d75831a4))
* **hooks:** accept string and list mcp tool responses ([eb96014](https://github.com/kaiketsu-labs/artifact-studio/commit/eb96014560f45b744cf3a9ac85dd770219792171))
* **hub:** separate reusable designs from project artifacts ([7e730b5](https://github.com/kaiketsu-labs/artifact-studio/commit/7e730b57dc3b3a301b7158cd45ad1539c0d023da))
* **mcp:** reuse rule versions across agent context reads ([961f818](https://github.com/kaiketsu-labs/artifact-studio/commit/961f818e99db717a6ef19d7d765b63fd3b6db372))
* **navigation:** show a persistent return to all artifacts ([f36270c](https://github.com/kaiketsu-labs/artifact-studio/commit/f36270c4ff8e930680e5eb7169b93fd24f5291ad))
* **presenter:** enter fullscreen directly from present ([3c7a067](https://github.com/kaiketsu-labs/artifact-studio/commit/3c7a06790a99ebb2200d8c973bafc7b3025b5514))
* **presenter:** keep progress points for long decks ([1c1827c](https://github.com/kaiketsu-labs/artifact-studio/commit/1c1827c367e5c245a7ab36e27041ccf671f214b1))
* **presenting:** support audience windows across browsers ([dadd6ce](https://github.com/kaiketsu-labs/artifact-studio/commit/dadd6ce4d95c8aa75f03cb0ca75bdf4662167dfe))
* **presenting:** support audience windows across browsers ([d3a21f6](https://github.com/kaiketsu-labs/artifact-studio/commit/d3a21f6b434b4ab9b5bdf14c018de5109c9fc3dd))
* **recovery:** exclude generated authoring dependencies from archives ([765c1a5](https://github.com/kaiketsu-labs/artifact-studio/commit/765c1a50ded156c395690d717257be37e8c8b7d0))
* **updater:** retain renderer cache for failed updates ([3b7518a](https://github.com/kaiketsu-labs/artifact-studio/commit/3b7518ae89ffb60edbd972569f3e35d8e4ba9814))


### Performance Improvements

* **agent:** reduce repeated context and idle review work ([7bf5298](https://github.com/kaiketsu-labs/artifact-studio/commit/7bf5298a2dc52bb93b248dfffcdd3551fb02162e))
* **editor:** coalesce zoom updates and support 400 percent ([44b434b](https://github.com/kaiketsu-labs/artifact-studio/commit/44b434b24946d7ec549824e90ee61e1034f4e82e))
* **hub:** keep discovery and startup context compact ([bb8ab1d](https://github.com/kaiketsu-labs/artifact-studio/commit/bb8ab1d0de6e361239c866ed723866caa8474260))
* **preview:** reuse loaded designs between slides ([d6b2df2](https://github.com/kaiketsu-labs/artifact-studio/commit/d6b2df22dd3b7d4ce08c569c517ef84592c5b8fe))
* **workspace:** reduce snapshot and idle refresh work ([f60db85](https://github.com/kaiketsu-labs/artifact-studio/commit/f60db8516c34fd97fb1c2d811855fb09b23bbd50))


### Documentation

* **agents:** keep rules actionable and separate learning evidence ([25cb1fb](https://github.com/kaiketsu-labs/artifact-studio/commit/25cb1fbfd66dfc03556c2310b9838d9e8ef51fc9))
* **qa:** record fresh clone verification ([c803bd5](https://github.com/kaiketsu-labs/artifact-studio/commit/c803bd50bb0a6e64d6ff142ae1061beff49da787))
* **qa:** record fresh github clone verification ([605c9c5](https://github.com/kaiketsu-labs/artifact-studio/commit/605c9c522cfb43c34dcc23296e80806f1fc13fef))
* refresh README and all app screenshots ([#18](https://github.com/kaiketsu-labs/artifact-studio/issues/18)) ([81a96f0](https://github.com/kaiketsu-labs/artifact-studio/commit/81a96f0cb8c76d359bdb9dada33c34986c9e1018))
* replace illustrated overview with technical Mermaid flowchart ([#30](https://github.com/kaiketsu-labs/artifact-studio/issues/30)) ([693555e](https://github.com/kaiketsu-labs/artifact-studio/commit/693555e24c73d88aa720d5e299ffb910ac8b1b86))


### Code Refactoring

* **brand:** rename the workspace to artifact studio ([985b94c](https://github.com/kaiketsu-labs/artifact-studio/commit/985b94c056f7870d00c1989b36c28f3293977f9f))
