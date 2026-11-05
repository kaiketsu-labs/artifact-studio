# Changelog

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
