# Contributing Guide
We welcome contributions from the community! If you would like to participate in the project, here are some things to consider.

## Code of Conduct
Please read and follow the [Code of Conduct](./CODE_OF_CONDUCT.md).

## Topics

* [Reporting Issues](#reporting-issues)
* [Submitting Pull Requests](#submitting-pull-requests)
* [License](#license)
* [Acknowledgements](#acknowledgements)
* [Project Maintainers](#project-maintainers)

## Reporting Issues

Before submitting a new issue, please review the existing open issues to ensure that the problem has not already been reported. If a similar issue is found, you are encouraged to contribute by providing your specific scenario or any relevant details to the ongoing discussion, or to subscribe in order to receive updates on its resolution. Please avoid comments like "+1" or "I have this issue too" without adding new information. Instead, use a 👍 emoji on the original issue. Older closed issues and PRs are automatically locked. If you encounter a similar problem, open a new issue instead of commenting on a closed one.

If you find a new issue or new bug with the project, we'd love to hear about it. The most important aspect of a bug report is that it includes enough information for us to reproduce it. Please create a bug report with the template as shown in this sample [bug template](https://gist.github.com/automationhacks/87b62440faf36d98ebbb732c372dd7c3). Not having all requested information makes it much harder to find and fix issues. A reproducible test case is the best thing you can include. It makes finding and fixing issues easier for [maintainers](#project-maintainers). The easier it is for us to reproduce a bug, the faster it'll be fixed. Please don't include any private/sensitive information in your issue. Security bugs should NOT be reported via GitHub and should instead be reported via the process described [here](SECURITY.md).

## Submitting Pull Requests

No Pull Request (PR) is too small! All contributions are welcome — whether it's fixing typos, improving comments, adding tests, resolving bugs, introducing features, or enhancing documentation.

Our projects follow the normal GitHub PR workflow for contributions. When contributing for the first time, the general workflow involves several steps: first, fork the project on GitHub and clone that fork to your local machine. Next, create a new branch, make your changes, and commit them. After that, push the branch to your fork and open a pull request (PR) against the upstream repository, including your host/guest image boot test logs, screenshots, or test results in the PR comments. You can find some easy tutorial online such as [this one](https://opensource.com/article/19/7/create-pull-request-github) and check out the official [GitHub docs](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/proposing-changes-to-your-work-with-pull-requests) that contain much more detail. All development happens on the `main` branch so all PRs should be submitted against that branch. Maintainers will take care of backporting if needed.

While bug fixes can first be identified via an "issue" in GitHub, that is not required. It's ok to just open a PR with the fix, but make sure you include the same information you would have included in an issue - like how to reproduce it.

PRs for new features should include some background on what use cases the new code is trying to address. When possible and when it makes sense, try to break up larger PRs into smaller ones - it's easier to review smaller code changes. But, only if those smaller ones make sense as stand-alone PRs.

When *adding new operating system images or modifying existing ones*, please use [mkosi](https://github.com/systemd/mkosi) to build your host/guest image and verify that it boots successfully on your system before submitting a pull request. This ensures that the functionality being introduced is working as expected prior to code review.

Regardless of the type of pull request, all PRs should include well-documented code changes, with appropriate comments within the code itself and high-quality commits.
For the high-quality commit messages, ensure that each message follows the [conventional commit style](https://www.conventionalcommits.org/en/v1.0.0/) and clearly explains *why* the change was made. Also, squash your commits into logical pieces of work that might want to be reviewed separate from the rest of the PRs. Refer to [squash-and-merge-your-commits section](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/incorporating-changes-from-a-pull-request/about-pull-request-merges#squash-and-merge-your-commits) for more information on squashing of your commits. Code changes, test and documentation updates should be part of the same commit as long as they are for the same feature/bug fix. Dependency updates are best kept in an individual commit.Totally unrelated changes, i.e. fixing typos in a different code part or adding a completely different feature should go into their own PR. Often squashing down to just one commit is acceptable since in the end the entire PR will be reviewed anyway.When in doubt, ask a maintainer how they prefer it. This repository follows a main branch protection rule for merges. PRs *need to be approved by the maintainers* of this repository. They will then be merged by a repo owner. A *review is required* for a pull request to merge.

### Sign your PRs

The `signed-off-by` is a line at the end of the commit message. Your signature certifies that you wrote the commit. Use a real name (sorry, no anonymous contributions).If you set your `user.name` and `user.email` git configs, you can sign your commit automatically with `git commit -s`.

### Code review

Once the PR is submitted, a maintainer will review it. Please ping a maintainer if nobody respond to it within 2 weeks.

Keep an eye out for the CI results on the PR. Detailed CI results for the PR can be found under the [sev-certify project GH Actions tab](https://github.com/AMDEPYC/sev-certify/actions). If all is well, then all GitHub workflows should succeed. If something failed, try to take a look at the logs for GH Action workflows under the [sev-certify project GH Actions tab](https://github.com/AMDEPYC/sev-certify/actions) to see if that it seems related. Then, try to fix your code or the test depending on the error message shown in the GH Action worklows.

After the reviewers and maintainers take a look, they will either write a comment stating `LGTM` (looks good to me) and approve the PR, in which case you do not need to make any further changes, or they write a comment with review feedback that you should address. If changes were requested, make them locally in your branch and then amend them into the commit from the PR. Please do not push extra commits that say things like "apply code review" or "fix x", where x is a bug introduced in a commit from your PR. Squash the change into the right commit to keep the git history clean.

Our project merges the commits as is and will not squash them on merge to preserve the full original context.

## License
Please go through our [project's license](../sev-certify/LICENSE)

## Acknowledgements
Thank you for wanting to contribute to the project! We appreciate the effort and time you are putting into your contribution.

## Project Maintainers

| Name        | GitHub Username |  Email   |
| :---------- | :-------------- | :---------- |
| Nathaniel McCallum    | @npmccallum        |  Nathaniel.McCallum@amd.com  |
| Diego GonzalezVillalobos  | @DGonzalezVillal     |  Diego.GonzalezVillalobos@amd.com  |
| Amanda Liem  | @amd-aliem    |  Amanda.Liem@amd.com  |
| Mark Gentry   | @markg-github     |  Mark.Gentry@amd.com  |