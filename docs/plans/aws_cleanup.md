# Plan: AWS account cleanup (unused for 90+ days)

Account `884520769610`, region `us-east-1`. Inventory taken **2026-10-08**. Status:
**proposed, nothing deleted yet**.

**Criterion.** Delete what has had no activity in 90 days *and* isn't needed by something
that does: no Lambda invocations, no IAM role use, no S3 writes, no IoT messages. Exceptions:

- **Keep anything owned by a CloudFormation stack that's in use.** Those resources go with
  their stack, never one by one.
- **Keep any client project with live data**, even when its Lambdas are idle. The UdePachuca,
  GOTA and IMTA buckets were written to today.

Each phase ends with a check. Run them in order. Nothing in phases 2–6 can be undone without
the backup from phase 0.

---

## Keep (in use)

| Area | Keep |
|---|---|
| Venko stacks | `sam-app`, `venko-akvo`, `venko-cmd4` (all their Lambdas, APIs, roles, buckets, rules) and `aws-sam-cli-managed-default` |
| Venko IoT | things `AKVO_cmd4`, `AKVO_Akvo`; policies `venko-cmd4-gateway`, `venko-akvo-gateway`; their certificates `e494e73e…`, `7d923eb5…` |
| Site 1 IoT | **certificate `3fcae758…`** (ACTIVE, 2022, attached to `AllIoTAcessPolicy`, no thing). Almost certainly the one the site 1 Pi connects with. **Do not touch** until the Pi is moved to its own cert/policy. |
| S3 with recent writes | `akvo-bucket`, `gotaproject`, `ghotrix-imta`, all `venko-*` (written 2026-10-08); `udepachuca` (2026-09-07) |
| Never-used but needed | `venko-akvo-IotRuleRole-*`: unused only because the akvo Pi isn't installed yet |

---

## Phase 0: Backup (before anything else)

Everything that phases 2–6 delete, saved to `~/aws-backup-2026-10-08/`:

```bash
B=~/aws-backup-$(date +%F); mkdir -p $B/{lambda,iam,iot,apigw,cfn}
# Lambdas: config + code zip
for f in $(aws lambda list-functions --query "Functions[].FunctionName" --output text); do
  aws lambda get-function --function-name $f > $B/lambda/$f.json
  curl -s -o $B/lambda/$f.zip "$(jq -r .Code.Location $B/lambda/$f.json)"
done
# IAM: every customer policy document (default version) and every role (trust + inline + attached)
aws iam get-account-authorization-details > $B/iam/authorization-details.json
# IoT: rules and policies
for r in $(aws iot list-topic-rules --query "rules[].ruleName" --output text); do aws iot get-topic-rule --rule-name $r > $B/iot/rule-$r.json; done
for p in $(aws iot list-policies --query "policies[].policyName" --output text); do aws iot get-policy --policy-name $p > $B/iot/policy-$p.json; done
# API Gateway exports and CloudFormation templates of stacks to delete
for s in slsAkvo-dev pygreet-dev CDKToolkit; do aws cloudformation get-template --stack-name $s > $B/cfn/$s.json; done
```

**Check:** every Lambda has a `.json` and a non-empty `.zip`, and
`authorization-details.json` is more than 1 MB.

---

## Phase 1: Security (urgent, independent of the cleanup)

| # | Finding | Action |
|---|---|---|
| 1.1 | **The AWS CLI on the dev machine uses the root account** (`arn:aws:iam::…:root`), and root has access keys. | Create an admin IAM user or IAM Identity Center user. Run `aws configure` with it and confirm with `aws sts get-caller-identity`. **Then** delete the root access key (Console → Security credentials). Do it in this order, or the CLI loses access. |
| 1.2 | IAM user **`Administrator`** has 2 **active** access keys, last used 2022-11 and 2023-02. | Deactivate both now (`aws iam update-access-key --status Inactive`) and delete them after 2 weeks. |
| 1.3 | Users **`chris`** and **`israel`** each have an **active** access key that has **never been used**. | Deactivate. Delete after 2 weeks if nobody complains. |
| 1.4 | Buckets **`ghotrix-imta`** and **`udepachuca`** are **public** (bucket policy), with versioning on. | Confirm with the owner that public read is intended. If not, turn on Block Public Access. |
| 1.5 | `AllIoTAcessPolicy` (site 1) allows every IoT action on every resource. | When the site 1 Pi is reachable, give it its own thing, cert and restricted policy, like akvo/cmd4 (VenkoDemo README, "Adding a site", step 2). Then retire cert `3fcae758…` and the policy. |

---

## Phase 2: CloudFormation test stacks (no risk)

| Stack | What | Last use |
|---|---|---|
| `slsAkvo-dev` | Serverless "hello" Lambda + public HTTP API | never invoked |
| `pygreet-dev` | 2 "hello/hallo" Lambdas + public HTTP API (`dev-pygreet`) | never invoked |
| `CDKToolkit` | CDK bootstrap; its S3 bucket is already gone and its ECR repo is empty | never used |

```bash
for s in slsAkvo-dev pygreet-dev CDKToolkit; do aws cloudformation delete-stack --stack-name $s; done
```

Deleting the stacks also deletes their 3 Lambdas, 2 HTTP APIs and IAM roles
(`pygreet-dev-us-east-1-lambdaRole`, `slsAkvo-dev-us-east-1-lambdaRole`, `cdk-hnb659fds-*`).
**Check:** `aws cloudformation list-stacks` shows `DELETE_COMPLETE`. If `CDKToolkit` fails
because of its missing bucket, retry with `--retain-resources` for that bucket.

---

## Phase 3: Broken and dead IoT rules + Lambdas

| Item | Why |
|---|---|
| Lambda **`AKVOSenseEvent`** + IoT rule **`AKVOSense`** (topic `AKVO`) | **100 % failing:** 1,599 errors in 90 days. It downloads `s3://udepachuca/AKVO1/sense.csv`, which doesn't exist. Last message 2026-09-06, from a pre-Venko gateway. |
| IoT rules **`Main`, `sandbox`, `IoT2Lambda`, `gota_s1_Sense`, `gota_s1_SYS`** | They call Lambdas that **don't exist** (`Main`, `sandbox`, `iot2Lambda`, `IoT2S3_Sense`, `IoT2S2_SYS`) and/or buckets that don't exist (`ghotrix`, `trainnonw3s`). None received a message in 90 days. |
| IoT rules **`S3_GOTA`, `S3_GOTHRIX`** | Write to buckets that don't exist (`trainnonw3s`, `ghotrix`). No messages in 90 days. |

```bash
aws lambda delete-function --function-name AKVOSenseEvent
for r in AKVOSense Main sandbox IoT2Lambda gota_s1_Sense gota_s1_SYS S3_GOTA S3_GOTHRIX; do aws iot delete-topic-rule --rule-name $r; done
```

**Check:** `aws iot list-topic-rules` keeps only the 6 Venko rules plus
`UdePachucaSense`, `UdePachucaSystem` and `imta_rule_data`.

---

## Phase 4: Unused Lambdas, their APIs and Amplify apps

**Test / demo / experiments**: 0 invocations in 90 days, no live trigger:

| Lambdas | Their API / app |
|---|---|
| `PandaTest`, `mytestfunction`, `layer_test`, `json2csv`, `Demo_Store_Data`, `DemoEvent2_CSV`, `Daily_task` | none (`Daily_task` has an S3 trigger permission, but no invocations) |
| `PowerOfMath` | REST API `PowerOfMathApi` (`fbyfsuj8uj`), Amplify app `PowerOfMath` |
| `APIgCORS-post`, `APIgCORSget` | REST APIs `API_Rest` (`pb404hoib6`), `APITest` (`854p0epqpl`); HTTP API `HTTPAPI` (`p775a2ex0h`) |
| `WebSocket` | WebSocket API `Websocket` (`tlxnrrkf0j`) |
| `WebAkvo` | HTTP API `WebAkvo` (`qpgsvl3w6k`) |
| *(none)* | Amplify app `FetchApi`: check that it's unused before deleting |

```bash
for f in PandaTest mytestfunction layer_test json2csv Demo_Store_Data DemoEvent2_CSV Daily_task \
         PowerOfMath APIgCORS-post APIgCORSget WebSocket WebAkvo; do aws lambda delete-function --function-name $f; done
for a in fbyfsuj8uj pb404hoib6 854p0epqpl; do aws apigateway delete-rest-api --rest-api-id $a; done
for a in p775a2ex0h tlxnrrkf0j qpgsvl3w6k; do aws apigatewayv2 delete-api --api-id $a; done
# Amplify: aws amplify list-apps → aws amplify delete-app --app-id <id>   (PowerOfMath; FetchApi after checking)
```

**Check:** `aws lambda list-functions` lists the 6 Venko functions plus the client-project
functions from phase 6.

---

## Phase 5: IAM and IoT leftovers

**IAM roles**: these role names belong to deleted or never-used functions and integrations,
**not used in 90+ days** (most never). Detach their policies, then delete:

`S3_GHOTRIX_Demo`, `S3_gota_file_mng`, `S3_GHOTRIX`, `S3_Lav_Sur`, `AWSCloud9SSMAccessRole`,
`CORSDEmo-role-*`, `APIgCORS-post-role-*` (×2), `APIgCORS-role-*`, `APIgCORSget-role-*`,
`PowerOfMath-role-*`, `mytestfunction-role-*`, `WebSocket-role-*`, `WebAkvo-role-*`,
`IoT2S2_SYS-role-*`, `IoT2S3_Sense-role-*`, `Main-role-*`, `SendMessage-role-*` (×2),
`email_ses-role-*`, `weekly_data-role-*`, `lambda-func-for-s3-v11-role-*`, `LambdaIoTPubRole`,
`iotanalysisrole`, `gota_SMS`, `sms`, `gota_s3_full_access`, `AmplifySSRLoggingRole-*`
(after the Amplify apps).

**Check before deleting:**
- `my-api-pool` (last used 2025-11-29) and `GhotrixCognito` / `us-east-2_WmJlGeib6-*`
  (Cognito, never used): the Cognito pool `User pool -ghotrix` still exists, so ask first.
- `AWS-QuickSetup-StackSet-*`: Systems Manager Quick Setup. Delete only if Quick Setup isn't
  configured.
- `GotaMainRole`, `GotaMainRole_Lambda`, `S3GetPutRole` are **still used** (2026-09/05) by
  the client projects. **Keep.**

```bash
r=ROLE_NAME
for p in $(aws iam list-attached-role-policies --role-name $r --query "AttachedPolicies[].PolicyArn" --output text); do aws iam detach-role-policy --role-name $r --policy-arn $p; done
for p in $(aws iam list-role-policies --role-name $r --query PolicyNames --output text); do aws iam delete-role-policy --role-name $r --policy-name $p; done
for p in $(aws iam list-instance-profiles-for-role --role-name $r --query "InstanceProfiles[].InstanceProfileName" --output text); do aws iam remove-role-from-instance-profile --role-name $r --instance-profile-name $p; done
aws iam delete-role --role-name $r
```

**IAM customer-managed policies**: **87 of 129 aren't attached to anything**, mostly
console-generated `AWSLambdaBasicExecutionRole-*`, `aws-iot-rule-*-action-*`,
`Amazon-EventBridge-Scheduler-Execution-Policy-*` and `IotAnalytics*`. After the roles above are
gone, delete every policy with `AttachmentCount == 0`. Old versions have to go first:

```bash
for arn in $(aws iam list-policies --scope Local --query "Policies[?AttachmentCount==\`0\`].Arn" --output text); do
  for v in $(aws iam list-policy-versions --policy-arn $arn --query "Versions[?!IsDefaultVersion].VersionId" --output text); do
    aws iam delete-policy-version --policy-arn $arn --version-id $v; done
  aws iam delete-policy --policy-arn $arn
done
```

**IoT test things, certs and policies**: all from 2026-01 tests or older:

| Thing | Cert | Policy | Action |
|---|---|---|---|
| `sss` | `99c64788…` ACTIVE | `sss-Policy` | deactivate cert → detach → delete cert, policy, thing |
| `iot_test` | `e028a105…` ACTIVE | `iot_test-Policy` | same |
| `iotTest` | `f2dcaa3f…` ACTIVE | `iotTest-Policy` | same |
| `DevCliTestThing` | none | `DevCliTestThingPolicy` (unattached) | delete policy + thing |
| `raspi4_101`, `raspi4_1` | none | none | delete thing |
| *(none)* | `91481cab…` INACTIVE (2023) | `MyNewESP32-Policy` | detach → delete cert, policy |
| *(none)* | `9d53c431…` INACTIVE (2022) | `AllIoTAcessPolicy` | detach → delete cert (**keep the policy**: site 1's active cert still uses it) |
| `AKVO_GW_001`, `Akvo_raspi4_101` | `ca7a3835…`, `aa366e2f…` ACTIVE (2026-04) | own policies | **ask first.** These look like Akvo test gateways from April. IoT doesn't record when a cert last connected (connectivity indexing is OFF), so deactivate them first and delete after 2 weeks if no device breaks. |

```bash
# per test cert: deactivate, detach policy and thing, delete
id=CERT_ID; arn=arn:aws:iot:us-east-1:884520769610:cert/$id
aws iot update-certificate --certificate-id $id --new-status INACTIVE
for p in $(aws iot list-attached-policies --target $arn --query "policies[].policyName" --output text); do aws iot detach-policy --policy-name $p --target $arn; done
for t in $(aws iot list-principal-things --principal $arn --query things --output text); do aws iot detach-thing-principal --thing-name $t --principal $arn; done
aws iot delete-certificate --certificate-id $id
```

---

## Phase 6: Client projects (ask the owner first)

Their Lambdas are idle, but their **buckets are written to daily**, so the projects are alive,
just not through those functions.

| Project | Lambdas (0 invocations in 90 d) | Still active |
|---|---|---|
| UdePachuca | `UdePachuca`, `UdePachucaReport`, `UdePachucaSense`, `UdePachucaSys` (**python3.10**, near end of AWS support), `UdePachuca_file_mgm` | rules `UdePachucaSense/System` enabled; bucket `udepachuca` (public) |
| GOTA | `Gota-Sense-Template`, `gota_data_report`, `gota_file_mgm` | bucket `gotaproject` (2.6 GB, written today) |
| IMTA | `imta-data` (24 MB of logs, so it used to be busy) | rule `imta_rule_data` enabled; bucket `ghotrix-imta` (public, written today) |

Proposal: confirm with each owner. Disable the idle rules/functions for 2 weeks
(`aws iot disable-topic-rule`), then delete them. **Don't delete any of these buckets.**

---

## Phase 7: CloudWatch logs

- Delete the log groups of every Lambda deleted above (`/aws/lambda/<name>`), plus the 6
  orphaned `/aws/lambda/amplify-d1slpip237olly-*`.
- **Venko:** add `AWS::Logs::LogGroup` with `RetentionInDays: 30` for both Lambdas in
  `infra/template.yaml`, then `sam deploy` each site. Their logs currently never expire.
- Optional: set 90-day retention on the client projects' log groups as well.

---

## After the cleanup

| | Before | After (phases 2–5) |
|---|---|---|
| CloudFormation stacks | 7 | 4 (Venko) |
| Lambdas | 31 | 15 (6 Venko + 9 client projects; 6 after phase 6) |
| IoT rules | 17 | 9 |
| API Gateways | 10 | 3 (Venko) |
| IAM customer policies | 129 | ~42 |
| Active access keys | root + 4 IAM | 1 admin IAM user |
